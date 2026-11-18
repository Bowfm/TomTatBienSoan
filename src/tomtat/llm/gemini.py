"""GeminiClient — gọi Gemini API thật với pool nhiều API Key."""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from . import base as _base
from .base import (
    BaseClient,
    LLMError,
    LLMResult,
    _COOLDOWN_S,
    _RPM_WINDOW_S,
    _parse_json,
    _redact,
    _status_of,
    is_transient,
    write_log,
)


def _get_quota_cycle_date() -> str:
    """Delegate tới tomtat.llm._get_quota_cycle_date để monkeypatch hoạt động đúng."""
    import tomtat.llm as _llm_pkg
    return _llm_pkg._get_quota_cycle_date()


class GeminiClient(BaseClient):
    """Client thật hỗ trợ xoay vòng nhiều API Key (Round-Robin & tự đổi key khi chạm giới hạn)."""

    def __init__(self, cfg: dict, api_key: str | list[str]):
        super().__init__(cfg)
        from google import genai  # nhập muộn để chế độ mock không cần SDK
        from google.genai._gaos.utils.retries import RetryConfig

        if isinstance(api_key, str):
            raw_keys = [api_key]
        else:
            raw_keys = list(api_key)
        self._api_keys = [k.strip() for k in raw_keys if k and k.strip()]
        if not self._api_keys:
            raise ValueError("Cần ít nhất một API Key hợp lệ.")

        self._key_index = 0
        self._key_lock = threading.Lock()
        self._clients: dict[str, genai.Client] = {}  # lazy init: tạo khi cần
        self._genai = genai
        self._retry_config_cls = RetryConfig
        self._exhausted: set[tuple[str, str]] = set()  # (api_key, model) đã cạn hạn mức trong ngày
        self._key_cooldown: dict[str, float] = {}       # key -> timestamp hết cooldown (sau 429)
        self._rpm_tracker: dict[str, list[float]] = {}  # key -> danh sách timestamp request gần đây

        self._api_key = self._api_keys[0]
        g = cfg["gemini"]
        self.model = g["model"]
        fallbacks = g.get("fallback_models") or []
        self.models_chain = [self.model] + [m for m in fallbacks if m and m != self.model]
        self.timeout = float(g.get("timeout_s", 90))
        self.max_retries = int(g.get("max_retries", 2))
        self.backoff_base = float(g.get("backoff_base_s", 2))
        self.backoff_max = float(g.get("backoff_max_s", 15))

        # File lưu danh sách (key, model) đã cạn quota trong ngày (theo chu kỳ 00:00 Pacific Time = 15:00 VN)
        work_path = cfg.work_dir if hasattr(cfg, "work_dir") else Path(cfg.get("paths", {}).get("work", "work"))
        self._exhausted_file = Path(work_path) / "quota_exhausted.json"
        self._current_cycle_date = _get_quota_cycle_date()
        self._load_exhausted()

    def _check_and_reset_cycle(self) -> None:
        """Tự động xóa danh sách cạn hạn mức khi bước sang chu kỳ ngày mới (15:00 VN)."""
        today = _get_quota_cycle_date()
        if today != self._current_cycle_date:
            self._current_cycle_date = today
            self._exhausted.clear()
            try:
                if self._exhausted_file.exists():
                    self._exhausted_file.unlink(missing_ok=True)
            except Exception:
                pass

    def _load_exhausted(self) -> None:
        if not self._exhausted_file.exists():
            return
        try:
            data = json.loads(self._exhausted_file.read_text(encoding="utf-8"))
            today = _get_quota_cycle_date()
            if data.get("date") == today:
                for item in data.get("exhausted", []):
                    k = item.get("key")
                    m = item.get("model")
                    if k in self._api_keys and m:
                        self._exhausted.add((k, m))
            else:
                self._exhausted_file.unlink(missing_ok=True)
        except Exception:
            pass

    def _mark_exhausted(self, key: str, model: str) -> None:
        with self._key_lock:
            self._check_and_reset_cycle()
            self._exhausted.add((key, model))
            try:
                today = self._current_cycle_date
                items = [{"key": k, "model": m} for (k, m) in self._exhausted]
                self._exhausted_file.parent.mkdir(parents=True, exist_ok=True)
                tmp = self._exhausted_file.with_suffix(".tmp")
                tmp.write_text(json.dumps({"date": today, "exhausted": items}, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(tmp, self._exhausted_file)
            except Exception:
                pass

    def _get_or_create_client(self, key: str):
        """Lazy init: chỉ tạo genai.Client khi key được dùng lần đầu, tiết kiệm RAM."""
        if key not in self._clients:
            c = self._genai.Client(api_key=key)
            try:
                c.interactions.sdk_configuration.retry_config = self._retry_config_cls("none", None, False)
            except Exception:
                pass
            self._clients[key] = c
        return self._clients[key]

    def _record_request(self, key: str) -> None:
        """Ghi nhận request vào bộ đếm RPM sliding window (thread-safe)."""
        with self._key_lock:
            self._rpm_tracker.setdefault(key, []).append(time.time())

    def _rpm_count(self, key: str) -> int:
        """Đếm request của key trong 60s gần nhất, dọn entry cũ. Gọi bên trong _key_lock."""
        cutoff = time.time() - _RPM_WINDOW_S
        if key in self._rpm_tracker:
            self._rpm_tracker[key] = [t for t in self._rpm_tracker[key] if t > cutoff]
            return len(self._rpm_tracker[key])
        return 0

    def _set_cooldown(self, key: str) -> None:
        """Đặt cooldown cho key sau khi bị 429 RPM limit."""
        self._key_cooldown[key] = time.time() + _COOLDOWN_S

    def _next_key_client(self) -> tuple[str, any]:
        with self._key_lock:
            k = self._api_keys[self._key_index % len(self._api_keys)]
            self._key_index += 1
            return k, self._get_or_create_client(k)

    def _get_available_key_client(self, model: str) -> tuple[str, any] | tuple[None, None]:
        with self._key_lock:
            self._check_and_reset_cycle()
            now = time.time()
            # Lọc: chưa cạn quota + đã hết cooldown
            candidates = [k for k in self._api_keys
                          if (k, model) not in self._exhausted
                          and self._key_cooldown.get(k, 0) <= now]
            if not candidates:
                # Fallback: bỏ qua cooldown (có thể key đã hết cooldown rồi mà float chênh)
                candidates = [k for k in self._api_keys if (k, model) not in self._exhausted]
            if not candidates:
                return None, None
            # Ưu tiên key có RPM thấp nhất (ít request gần đây → ít rủi ro bị 429)
            rpm_counts = {k: self._rpm_count(k) for k in candidates}
            min_rpm = min(rpm_counts.values())
            best = [k for k in candidates if rpm_counts[k] <= min_rpm + 1]
            k = best[self._key_index % len(best)]
            self._key_index += 1
            return k, self._get_or_create_client(k)

    def _gen_config(self, step: str, model_name: str = "") -> dict:
        g = self.cfg["gemini"]
        gc: dict = {}
        lvl = (g.get("thinking_level") or {}).get(step)
        # thinking_level chỉ áp dụng cho dòng Gemini 3.x và 4.x
        if lvl and not model_name.startswith("gemini-2."):
            gc["thinking_level"] = lvl
        if g.get("max_output_tokens"):
            gc["max_output_tokens"] = int(g["max_output_tokens"])
        if g.get("temperature") is not None:
            gc["temperature"] = float(g["temperature"])
        return gc

    def _call_once(self, model_name, step, system, prompt, schema, client=None):
        if client is None:
            _, client = self._next_key_client()
        resp = client.interactions.create(
            model=model_name,
            input=prompt,
            system_instruction=system,
            response_format={"type": "text", "mime_type": "application/json", "schema": schema},
            generation_config=self._gen_config(step, model_name),
            store=False,
            timeout=self.timeout,
        )
        text = getattr(resp, "output_text", None) or ""
        usage = {}
        u = getattr(resp, "usage", None)
        if u is not None:
            for k in ("total_input_tokens", "total_output_tokens", "total_thought_tokens",
                      "total_cached_tokens", "total_tokens"):
                usage[k] = getattr(u, k, None)
        status = getattr(resp, "status", None)
        return text, usage, status

    def generate_json(self, step, system, prompt, schema, *, log_dir=None, attempt=0, context=None):
        last_exc: BaseException | None = None
        for model_idx, cur_model in enumerate(self.models_chain):
            # Với mỗi model: tận dụng tối đa các key còn hạn mức trong pool
            tries_left = max(len(self._api_keys) * 2, self.max_retries + 1)
            model_503_count = 0
            while tries_left > 0:
                tries_left -= 1
                cur_key, cur_client = self._get_available_key_client(cur_model)
                if cur_key is None:
                    # Toàn bộ key trong pool đã cạn quota ngày cho cur_model -> trượt xuống model tiếp theo
                    break

                t0 = time.time()
                record = {"step": step, "attempt": attempt, "model": cur_model,
                          "started_at": datetime.now().isoformat(timespec="seconds"),
                          "generation_config": self._gen_config(step, cur_model),
                          "system": system, "prompt": prompt, "schema": schema}
                try:
                    with self._semaphore:
                        text, usage, status = self._call_once(cur_model, step, system, prompt, schema, client=cur_client)
                    self._record_request(cur_key)
                    dur = time.time() - t0
                    record.update(duration_s=round(dur, 2), response_text=text, usage=usage, status=str(status))
                    data = _parse_json(text)
                    record["parsed_ok"] = True
                    write_log(log_dir, record, self._api_keys)
                    return LLMResult(data=data, text=text, usage=usage, duration_s=dur, model=cur_model)
                except json.JSONDecodeError as exc:
                    record.update(duration_s=round(time.time() - t0, 2), error=f"JSON không hợp lệ: {exc}")
                    write_log(log_dir, record, self._api_keys)
                    last_exc = exc
                    break
                except Exception as exc:  # noqa: BLE001
                    err_str = _redact(f"{type(exc).__name__}: {exc}", self._api_keys)
                    record.update(duration_s=round(time.time() - t0, 2), error=err_str)
                    write_log(log_dir, record, self._api_keys)
                    last_exc = exc
                    err_lower = err_str.lower()
                    is_quota_exhausted = (
                        "per day" in err_lower
                        or "perday" in err_lower
                        or "limit: 0" in err_str
                        or "exceeded a quota" in err_lower
                        or "quota exceeded" in err_lower
                        or "resource has been exhausted" in err_lower
                    )
                    if is_quota_exhausted:
                        # Key này đã cạn quota của cur_model -> đánh dấu để chuyển sang key khác trong pool
                        self._mark_exhausted(cur_key, cur_model)
                        continue
                    if _status_of(exc) in (401, 403) or "unauthenticated" in err_lower or "not valid" in err_lower:
                        # Key này lỗi xác thực/sai key -> loại bỏ khỏi danh sách và chuyển ngay sang key khác trong pool
                        for m in self.models_chain:
                            self._mark_exhausted(cur_key, m)
                        continue
                    if _status_of(exc) == 404:
                        break
                    if _status_of(exc) in (502, 503) and ("high demand" in err_lower or "service_unavailable" in err_lower):
                        model_503_count += 1
                        if model_503_count >= 3:
                            # Model đang quá tải diện rộng -> chuyển ngay sang model tiếp theo trong chain
                            break
                    if not is_transient(exc):
                        break
                    if _status_of(exc) == 429:
                        # 429 tạm thời (RPM) -> cooldown key 30s và chuyển ngay sang key khác
                        self._set_cooldown(cur_key)
                        continue
                    time.sleep(min(self.backoff_max, self.backoff_base))
        msg = _redact(f"{type(last_exc).__name__}: {last_exc}", self._api_keys)
        st = _status_of(last_exc) if last_exc else None
        hint = ""
        if st in (401, 403):
            hint = " (kiểm tra GEMINI_API_KEY và quyền truy cập model)"
        elif st == 402:
            hint = " (Hết số dư tiền nạp trước trên Google AI Studio; vui lòng nạp thêm tiền tại https://ai.studio/projects hoặc https://ai.google.dev/gemini-api/docs/billing#prepay)"
        elif st == 404:
            hint = f" (model '{self.model}' không tồn tại hoặc tài khoản chưa được dùng; đổi gemini.model trong config.yaml)"
        elif st == 429:
            hint = " (vượt giới hạn tốc độ/hạn mức; giảm --workers hoặc chờ)"
        raise LLMError(f"Gọi Gemini thất bại ở bước {step}: {msg}{hint}")
