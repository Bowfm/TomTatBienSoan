"""Gọi Gemini (SDK google-genai, Interactions API) và client giả lập cho chế độ --mock."""
from __future__ import annotations

import json
import os
import random
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import STYLES
from .textutil import count_words, lines_of, split_sentences

TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504}


class LLMError(Exception):
    """Lỗi gọi model (đã hết số lần thử hoặc lỗi không thể thử lại)."""


@dataclass
class LLMResult:
    data: dict
    text: str
    usage: dict = field(default_factory=dict)
    duration_s: float = 0.0
    model: str = ""


def _status_of(exc: BaseException) -> int | None:
    for attr in ("status_code", "code", "status"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    resp = getattr(exc, "response", None)
    v = getattr(resp, "status_code", None)
    return v if isinstance(v, int) else None


def is_transient(exc: BaseException) -> bool:
    st = _status_of(exc)
    if st is not None:
        return st in TRANSIENT_STATUS
    name = type(exc).__name__.lower()
    if any(k in name for k in ("timeout", "connection", "connect", "network", "remoteprotocol",
                               "readerror", "noresponse", "ratelimit", "unavailable")):
        return True
    return isinstance(exc, (TimeoutError, ConnectionError))


def _redact(text: str, secret: str | list[str] | None) -> str:
    if not text or not secret:
        return text
    secrets = [secret] if isinstance(secret, str) else secret
    for s in secrets:
        if s and isinstance(s, str):
            text = text.replace(s, "***")
    return text


def _get_quota_cycle_date() -> str:
    """Trả về ngày theo chu kỳ reset hạn mức của Google Gemini (00:00 Pacific Time, tức 15h VN mùa đông / 14h VN mùa hè)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
    except Exception:
        pass
    # Tự tính chuẩn theo múi giờ US/Pacific (PST UTC-8 / PDT UTC-7) không phụ thuộc thư viện ngoài
    import calendar
    from datetime import datetime, timedelta, timezone

    now_utc = datetime.now(timezone.utc)
    year = now_utc.year
    w_mar = calendar.weekday(year, 3, 1)
    dst_start = datetime(year, 3, 1 + (6 - w_mar) % 7 + 7, 10, 0, tzinfo=timezone.utc)
    w_nov = calendar.weekday(year, 11, 1)
    dst_end = datetime(year, 11, 1 + (6 - w_nov) % 7, 9, 0, tzinfo=timezone.utc)

    offset_hours = -7 if dst_start <= now_utc < dst_end else -8
    pt_time = now_utc + timedelta(hours=offset_hours)
    return pt_time.strftime("%Y-%m-%d")


def write_log(log_dir: Path | None, record: dict, secret: str | list[str] | None = None) -> None:
    if not log_dir:
        return
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    name = f"{ts}_{record.get('step', 'call')}_{record.get('attempt', 0)}.json"
    body = json.dumps(record, ensure_ascii=False, indent=1, default=str)
    (log_dir / name).write_text(_redact(body, secret), encoding="utf-8")


class BaseClient:
    model = "base"
    _semaphore: threading.Semaphore | None = None
    _sem_limit: int = 0

    def __init__(self, cfg: dict):
        self.cfg = cfg
        g = cfg.get("gemini", {})
        limit = max(1, int(g.get("max_concurrent_calls", 4)))
        if BaseClient._semaphore is None or BaseClient._sem_limit != limit:
            BaseClient._semaphore = threading.Semaphore(limit)
            BaseClient._sem_limit = limit

    def generate_json(self, step: str, system: str, prompt: str, schema: dict, *,
                      log_dir: Path | None = None, attempt: int = 0,
                      context: dict | None = None) -> LLMResult:
        raise NotImplementedError


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
        self._clients: dict[str, genai.Client] = {}
        self._exhausted: set[tuple[str, str]] = set()  # (api_key, model) đã cạn hạn mức trong ngày

        for k in self._api_keys:
            c = genai.Client(api_key=k)
            try:
                c.interactions.sdk_configuration.retry_config = RetryConfig("none", None, False)
            except Exception:
                pass
            self._clients[k] = c

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
                    if k in self._clients and m:
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

    def _next_key_client(self) -> tuple[str, any]:
        with self._key_lock:
            k = self._api_keys[self._key_index % len(self._api_keys)]
            self._key_index += 1
            return k, self._clients[k]

    def _get_available_key_client(self, model: str) -> tuple[str, any] | tuple[None, None]:
        with self._key_lock:
            self._check_and_reset_cycle()
            available = [k for k in self._api_keys if (k, model) not in self._exhausted]
            if not available:
                return None, None
            k = available[self._key_index % len(available)]
            self._key_index += 1
            return k, self._clients[k]

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
                        # 429 tạm thời (RPM) -> chuyển ngay sang key khác trong pool không cần chờ
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


def _parse_json(text: str) -> dict:
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    return json.loads(t)


# ====================================================================== giả lập

class MockClient(BaseClient):
    """Client giả lập: trả về JSON hợp lệ theo luật đơn giản, không gọi mạng."""

    model = "mock"

    def __init__(self, cfg: dict, delay_s: float = 0.0):
        super().__init__(cfg)
        self.delay_s = delay_s
        self.calls: list[str] = []

    def generate_json(self, step, system, prompt, schema, *, log_dir=None, attempt=0, context=None):
        t0 = time.time()
        if self.delay_s:
            time.sleep(self.delay_s)
        ctx = context or {}
        fn = getattr(self, f"_mock_{step}")
        data = fn(ctx, attempt)
        self.calls.append(step)
        text = json.dumps(data, ensure_ascii=False)
        usage = {"total_input_tokens": len(prompt) // 4, "total_output_tokens": len(text) // 4,
                 "total_thought_tokens": 0}
        write_log(log_dir, {"step": step, "attempt": attempt, "model": "mock",
                            "started_at": datetime.now().isoformat(timespec="seconds"),
                            "duration_s": round(time.time() - t0, 3), "system": system,
                            "prompt": prompt, "schema": schema, "response_text": text,
                            "usage": usage, "parsed_ok": True})
        return LLMResult(data=data, text=text, usage=usage, duration_s=time.time() - t0, model="mock")

    # --- từng bước ---
    @staticmethod
    def _guess_style(text: str) -> str:
        low = text.lower()
        if re.search(r"\bđiều \d+\.", low) or "căn cứ" in low:
            return "hành chính - công vụ"
        if re.search(r"\b(tôi|mình|bạn)\b", low):
            return "sinh hoạt hằng ngày"
        if re.search(r"\b(nghiên cứu|phương pháp|kết quả cho thấy)\b", low):
            return "khoa học"
        if re.search(r"\b(đảng|chủ trương|đường lối)\b", low):
            return "chính luận"
        return "báo chí - công luận"

    def _mock_label(self, ctx, attempt):
        data = ctx["data"]
        per = []
        for d in data["docs"]:
            txt = " ".join(u["text"] for u in d["units"])
            per.append({"doc": d["n"], "label": self._guess_style(txt), "reason": "Giả lập: theo từ khóa."})
        totals: dict[str, int] = {}
        for p, d in zip(per, data["docs"]):
            totals[p["label"]] = totals.get(p["label"], 0) + d["words"]
        label = max(totals, key=totals.get) if totals else STYLES[0]
        return {"label": label, "per_doc": per, "confidence": "cao",
                "reason": "Giả lập (--mock): nhãn đoán theo từ khóa, không phải kết quả AI."}

    def _mock_extract(self, ctx, attempt):
        data, target = ctx["data"], ctx.get("target_ratio", 0.3)
        ai_ext = (data.get("ai_extract") or "").lower()
        sels = []
        for d in data["docs"]:
            goal = d["words"] * target
            # Ưu tiên các câu xuất hiện trong ai_extract trước để đạt ROUGE-2 >= 33%
            ranked = sorted(
                d["units"],
                key=lambda u: (0 if u["text"][:25].lower() in ai_ext else 1, u["id"]),
            )
            picked, w = [], 0
            for u in ranked:
                if w + u["words"] <= goal * 1.05:
                    picked.append(u["id"])
                    w += u["words"]
            if not picked and d["units"]:
                picked = [d["units"][0]["id"]]
            sels.append({"doc": d["n"], "ids": sorted(picked)})
        return {"selections": sels, "note": "Giả lập: chọn các câu trọng tâm theo thứ tự gốc."}

    def _mock_abstract(self, ctx, attempt):
        extract = ctx.get("extract", "")
        t = ctx.get("targets", {})
        goal = (t.get("short_low", 0) + t.get("short_high", 0)) / 2 or 50
        sents = []
        for ln in lines_of(extract):
            for s in split_sentences(ln):
                s = re.sub(r"^\s*(\d+[/.)]|[-•*])\s*", "", s).strip()
                s = re.sub(r"[=:;]+\)+|:v|[🍀]+", "", s).strip()
                if s:
                    sents.append(s)
        out, w = [], 0
        for s in sents:
            n = count_words(s)
            if w + n > goal and out:
                continue
            out.append(s)
            w += n
        text = " ".join(out)
        text = re.sub(r"\s+([,.;:!?])", r"\1", text)
        text = re.sub(r"[ \t]{2,}", " ", text).strip()
        if text and text[-1] not in ".!?…":
            text += "."
        return {"abstract": text, "note": "Giả lập: ghép các câu đầu của bản trích chọn."}

    def _mock_judge(self, ctx, attempt):
        return {"relevance": 4.5, "coherence": 4.5, "consistency": 5, "fluency": 4.5,
                "issues": [], "summary": "Giả lập (--mock): không chấm thật."}
