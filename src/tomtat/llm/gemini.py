"""GeminiClient — gọi Gemini API thật với pool nhiều API Key."""
from __future__ import annotations

import hashlib
import json
import os
import random
import threading
import time
from datetime import datetime
from pathlib import Path

from . import base as _base
from .base import (
    BaseClient,
    LLMCancelledError,
    LLMError,
    LLMResult,
    _COOLDOWN_S,
    _QUOTA_FILE_LOCK,
    _RPM_WINDOW_S,
    _error_consumes_rpd,
    _heal_qps_exhausted_items,
    _is_connection_error,
    _is_payload_qps_429,
    _is_timeout_error,
    _parse_json,
    _parse_timeout_value,
    _reconcile_rpd_from_logs,
    _redact,
    _status_of,
    check_not_cancelled,
    get_active_cancel_event,
    get_model_min_interval_s,
    get_model_rpd_per_key,
    get_model_rpm_per_key,
    get_model_timeout_s,
    is_transient,
    sleep_with_cancel,
    write_log,
)

_UNSET = object()


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
        self._rpd_counts: dict[str, int] = {}          # model -> số request đã sử dụng trong ngày (kể cả 503/Timeout/JSON lỗi)
        self._key_rpd_counts: dict[tuple[str, str], int] = {}  # (key, model) -> số request đã gọi trong ngày
        self._key_cooldown: dict[str, float] = {}       # key -> timestamp hết cooldown (sau 429)
        self._rpm_tracker: dict[str, list[float]] = {}  # key -> danh sách timestamp request gần đây (chung cho key)
        self._model_rpm_tracker: dict[tuple[str, str], list[float]] = {}  # (key, model) -> timestamps request gần đây
        self._next_allowed_ts: dict[tuple[str, str], float] = {}          # (key, model) -> thời điểm sớm nhất được gọi tiếp
        self._idle_429_strikes: dict[tuple[str, str], int] = {}  # đếm số lần lỗi 429 theo từng cặp (key, model)
        self._model_cursor: dict[str, int] = {}  # model -> chỉ số key tiếp theo sẽ gọi (gọi tuần tự #1 -> #N rồi quay vòng)
        self._payload_next_ts: dict[str, float] = {}  # payload_sig -> thời điểm sớm nhất được gửi lại payload giống nhau
        self._global_qps_next_ts: float = 0.0  # thời điểm sớm nhất cổng chung hết hạ nhiệt sau lỗi 429 QPS
        self._dispatch_slots: list[float] = []  # mốc thời gian gửi API gần nhất/đã đặt chỗ trên toàn bộ luồng
        self._model_503_streak: dict[str, int] = {}  # số lần lỗi 503/tạm thời liên tiếp trên toàn pool theo từng model
        self._model_outage_until: dict[str, float] = {}  # model -> timestamp hết thời gian tạm hạ nhiệt sau bão 503

        self._api_key = self._api_keys[0]
        self._tls_timeout = threading.local()
        self._cfg_gemini_snapshot: dict | None = None
        self._apply_gemini_config(cfg.get("gemini") or {})

        # File lưu danh sách (key, model) đã cạn quota và bộ đếm RPD trong ngày (theo chu kỳ 00:00 Pacific Time = 15:00 VN)
        work_path = cfg.work_dir if hasattr(cfg, "work_dir") else Path(cfg.get("paths", {}).get("work", "work"))
        self._exhausted_file = Path(work_path) / "quota_exhausted.json"
        self._current_cycle_date = _get_quota_cycle_date()
        self._reset_prefix: str = ""
        self._load_exhausted()

    @staticmethod
    def _snapshot_gemini_cfg(g: dict) -> dict:
        return {
            "model": g.get("model"),
            "fallback_models": list(g.get("fallback_models") or []),
            "timeout_s": g.get("timeout_s"),
            "timeout_s_by_model": dict(g.get("timeout_s_by_model") or {}) if isinstance(g.get("timeout_s_by_model"), dict) else g.get("timeout_s_by_model"),
            "max_retries": g.get("max_retries"),
            "backoff_base_s": g.get("backoff_base_s"),
            "backoff_max_s": g.get("backoff_max_s"),
            "max_rounds_per_model": g.get("max_rounds_per_model"),
            "max_timeouts_per_model": g.get("max_timeouts_per_model"),
            "max_transient_per_model": g.get("max_transient_per_model"),
            "model_outage_threshold": g.get("model_outage_threshold"),
            "model_outage_cooldown_s": g.get("model_outage_cooldown_s"),
            "global_min_dispatch_interval_s": g.get("global_min_dispatch_interval_s"),
            "qps_global_cooldown_s": g.get("qps_global_cooldown_s"),
        }

    def _apply_gemini_config(self, g: dict) -> None:
        self.model = g["model"]
        fallbacks = g.get("fallback_models") or []
        self.models_chain = [self.model] + [m for m in fallbacks if m and m != self.model]
        if "timeout_s" in g:
            raw_t = g.get("timeout_s")
            if isinstance(raw_t, dict):
                self.timeout: float | None = None
            else:
                _, parsed_t = _parse_timeout_value(raw_t)
                self.timeout = max(1.0, parsed_t) if parsed_t is not None else None
        else:
            self.timeout = None
        self.max_retries = int(g.get("max_retries", 2))
        self.backoff_base = float(g.get("backoff_base_s", 2))
        self.backoff_max = float(g.get("backoff_max_s", 15))
        try:
            self.max_rounds_per_model = max(0, int(g.get("max_rounds_per_model", 3)))
        except (TypeError, ValueError):
            self.max_rounds_per_model = 3
        try:
            self.max_timeouts_per_model = max(0, int(g.get("max_timeouts_per_model", 2)))
        except (TypeError, ValueError):
            self.max_timeouts_per_model = 2
        try:
            self.max_transient_per_model = max(0, int(g.get("max_transient_per_model", 8)))
        except (TypeError, ValueError):
            self.max_transient_per_model = 8
        try:
            self.model_outage_threshold = max(0, int(g.get("model_outage_threshold", 5)))
        except (TypeError, ValueError):
            self.model_outage_threshold = 5
        try:
            self.model_outage_cooldown_s = max(0.0, float(g.get("model_outage_cooldown_s", 30.0)))
        except (TypeError, ValueError):
            self.model_outage_cooldown_s = 30.0
        try:
            self.global_min_dispatch_interval_s = max(0.0, float(g.get("global_min_dispatch_interval_s", 1.5)))
        except (TypeError, ValueError):
            self.global_min_dispatch_interval_s = 1.5
        try:
            self.qps_global_cooldown_s = max(0.0, float(g.get("qps_global_cooldown_s", 15.0)))
        except (TypeError, ValueError):
            self.qps_global_cooldown_s = 15.0
        self._cfg_gemini_snapshot = self._snapshot_gemini_cfg(g)

    def sync_config(self, new_cfg: dict | None = None) -> None:
        """Đồng bộ nóng cấu hình gemini (timeout, fallback_models, max_timeouts_per_model...) nếu config.yaml được nạp lại."""
        with self._key_lock:
            if new_cfg is not None:
                self.cfg = new_cfg
            g = self.cfg.get("gemini") or {}
            if not g.get("model"):
                return
            snap = self._snapshot_gemini_cfg(g)
            if snap != self._cfg_gemini_snapshot:
                self._apply_gemini_config(g)

    def sync_keys(self, new_keys: list[str] | None = None) -> int:
        """Đồng bộ nóng danh sách API keys từ api_keys.txt / biến môi trường mà không làm mất lịch sử RPM/RPD đang chạy."""
        from .. import config as _cfg_mod
        from ..config import get_pool_scaling

        if new_keys is None:
            new_keys = _cfg_mod.get_api_keys()
        cleaned = [k.strip() for k in (new_keys or []) if k and k.strip()]
        if not cleaned:
            return len(self._api_keys)

        with self._key_lock:
            if cleaned != self._api_keys:
                self._api_keys = cleaned
                self._api_key = cleaned[0]
                scaling = get_pool_scaling(self.cfg, api_keys=cleaned)
                BaseClient.ensure_concurrency_limit(scaling["max_concurrent_calls"])
        return len(self._api_keys)

    def _save_quota_state_unlocked(self) -> None:
        """Lưu trạng thái cạn quota và số lượt RPD trong ngày xuống đĩa (gọi bên trong _key_lock)."""
        with _QUOTA_FILE_LOCK:
            try:
                now = time.time()
                today = self._current_cycle_date
                items = [{"key": k, "model": m} for (k, m) in self._exhausted]
                next_allowed = [
                    {"key": k, "model": m, "ts": round(ts, 3)}
                    for (k, m), ts in self._next_allowed_ts.items()
                    if ts > now
                ]
                cooldowns = {
                    k: round(ts, 3)
                    for k, ts in self._key_cooldown.items()
                    if ts > now
                }
                strikes_429 = [
                    {"key": k, "model": m, "count": cnt}
                    for (k, m), cnt in self._idle_429_strikes.items()
                    if cnt > 0 and (k, m) not in self._exhausted
                ]
                payload = {
                    "date": today,
                    "schema_version": 2,
                    "exhausted": items,
                    "rpd_counts": dict(self._rpd_counts),
                    "cursors": dict(self._model_cursor),
                    **({"next_allowed_ts": next_allowed} if next_allowed else {}),
                    **({"key_cooldown": cooldowns} if cooldowns else {}),
                    **({"strikes_429": strikes_429} if strikes_429 else {}),
                    **({"reset_prefix": self._reset_prefix} if self._reset_prefix else {}),
                }
                self._exhausted_file.parent.mkdir(parents=True, exist_ok=True)
                tmp = self._exhausted_file.with_suffix(f".{threading.get_ident()}.tmp")
                tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                for attempt in range(4):
                    try:
                        os.replace(tmp, self._exhausted_file)
                        break
                    except PermissionError:
                        if attempt == 3:
                            tmp.unlink(missing_ok=True)
                            raise
                        time.sleep(0.01 * (attempt + 1))
            except Exception:
                pass

    def _check_and_reset_cycle(self) -> None:
        """Tự động xóa danh sách cạn hạn mức và bộ đếm RPD khi bước sang chu kỳ ngày mới (15:00 VN)."""
        today = _get_quota_cycle_date()
        if today != self._current_cycle_date:
            self._current_cycle_date = today
            self._exhausted.clear()
            self._rpd_counts.clear()
            self._key_rpd_counts.clear()
            self._key_cooldown.clear()
            self._rpm_tracker.clear()
            self._model_rpm_tracker.clear()
            self._next_allowed_ts.clear()
            self._idle_429_strikes.clear()
            self._model_cursor.clear()
            self._payload_next_ts.clear()
            self._global_qps_next_ts = 0.0
            self._dispatch_slots.clear()
            self._model_503_streak.clear()
            self._model_outage_until.clear()
            self._reset_prefix = ""
            with _QUOTA_FILE_LOCK:
                try:
                    if self._exhausted_file.exists():
                        self._exhausted_file.unlink(missing_ok=True)
                except Exception:
                    pass

    def _load_exhausted(self) -> None:
        today = _get_quota_cycle_date()
        now = time.time()
        loaded_schema_v = 2
        with _QUOTA_FILE_LOCK:
            if self._exhausted_file.exists():
                try:
                    data = json.loads(self._exhausted_file.read_text(encoding="utf-8"))
                    if data.get("date") == today:
                        loaded_schema_v = int(data.get("schema_version") or 1)
                        self._reset_prefix = str(data.get("reset_prefix") or "")
                        for item in data.get("exhausted", []):
                            k = item.get("key")
                            m = item.get("model")
                            if k in self._api_keys and m:
                                self._exhausted.add((k, m))
                        for m_name, cnt in (data.get("rpd_counts") or {}).items():
                            if m_name and isinstance(cnt, (int, float)):
                                self._rpd_counts[str(m_name)] = max(0, int(cnt))
                        for m_name, cur in (data.get("cursors") or {}).items():
                            if m_name and isinstance(cur, int) and cur >= 0:
                                self._model_cursor[str(m_name)] = cur
                        for item in data.get("next_allowed_ts", []):
                            k = item.get("key")
                            m = item.get("model")
                            ts = item.get("ts")
                            if k in self._api_keys and m and isinstance(ts, (int, float)) and ts > now:
                                self._next_allowed_ts[(k, m)] = float(ts)
                        for k, ts in (data.get("key_cooldown") or {}).items():
                            if k in self._api_keys and isinstance(ts, (int, float)) and ts > now:
                                self._key_cooldown[k] = float(ts)
                        for item in data.get("strikes_429", []):
                            k = item.get("key")
                            m = item.get("model")
                            cnt = item.get("count")
                            if k in self._api_keys and m and isinstance(cnt, int) and cnt > 0:
                                self._idle_429_strikes[(k, m)] = cnt
                    else:
                        self._exhausted_file.unlink(missing_ok=True)
                except Exception:
                    pass
        changed = False
        # Tự phục hồi các cặp (key, model) bị khóa oan do lỗi 429 QPS payload trùng lặp ở phiên bản cũ
        if loaded_schema_v < 2 and self._exhausted:
            try:
                cur_items = [{"key": k, "model": m} for (k, m) in self._exhausted]
                healed_items, healed = _heal_qps_exhausted_items(
                    self._exhausted_file.parent, today, cur_items, min_prefix=self._reset_prefix
                )
                if healed:
                    self._exhausted = {(x["key"], x["model"]) for x in healed_items}
                    changed = True
            except Exception:
                pass
        if loaded_schema_v < 2 and self._exhausted_file.exists():
            changed = True
        # Đối soát với log thực tế trong ngày (bắt trọn cả các lượt lỗi 503, Timeout, JSON)
        try:
            log_counts = _reconcile_rpd_from_logs(
                self._exhausted_file.parent, today, min_prefix=self._reset_prefix
            )
            for m_name, l_cnt in log_counts.items():
                if l_cnt > self._rpd_counts.get(m_name, 0):
                    self._rpd_counts[m_name] = l_cnt
                    changed = True
        except Exception:
            pass
        if changed:
            self._save_quota_state_unlocked()

    def _mark_exhausted(self, key: str, model: str) -> None:
        with self._key_lock:
            self._check_and_reset_cycle()
            self._exhausted.add((key, model))
            self._save_quota_state_unlocked()

    @staticmethod
    def _build_http_options() -> dict | None:
        """Tạo http_options với read=None (không cắt timeout khi model đang suy nghĩ) nhưng giữ connect/write/pool timeout."""
        for mod_name in ("httpx2", "httpx"):
            try:
                mod = __import__(mod_name)
                timeout_cls = getattr(mod, "Timeout", None)
                if timeout_cls is not None:
                    return {
                        "client_args": {
                            "timeout": timeout_cls(timeout=None, connect=30.0, write=60.0, pool=30.0)
                        }
                    }
            except ImportError:
                continue
        return None

    def _get_or_create_client(self, key: str):
        """Lazy init: chỉ tạo genai.Client khi key được dùng lần đầu, tiết kiệm RAM."""
        if key not in self._clients:
            http_opts = self._build_http_options()
            if http_opts is not None:
                try:
                    c = self._genai.Client(api_key=key, http_options=http_opts)
                except TypeError:
                    c = self._genai.Client(api_key=key)
            else:
                c = self._genai.Client(api_key=key)
            try:
                c.interactions.sdk_configuration.retry_config = self._retry_config_cls("none", None, False)
            except Exception:
                pass
            self._clients[key] = c
        return self._clients[key]

    def _evict_client(self, key: str) -> None:
        """Loại bỏ client khỏi cache khi gặp timeout/lỗi kết nối để lượt gọi sau tạo kết nối HTTP sạch."""
        with self._key_lock:
            self._clients.pop(key, None)

    def _record_request(self, key: str, model: str | None = None, *, count_rpd: bool = True) -> None:
        """Ghi nhận request vào bộ đếm RPM sliding window và bộ đếm RPD trong ngày (thread-safe)."""
        with self._key_lock:
            self._check_and_reset_cycle()
            now = time.time()
            self._rpm_tracker.setdefault(key, []).append(now)
            if model:
                self._model_rpm_tracker.setdefault((key, model), []).append(now)
                min_interval = get_model_min_interval_s(model, self.cfg)
                cur_allowed = self._next_allowed_ts.get((key, model), 0.0)
                if now + min_interval > cur_allowed:
                    self._next_allowed_ts[(key, model)] = now + min_interval
                if count_rpd:
                    self._increment_rpd_unlocked(model, key)

    def _increment_rpd_unlocked(self, model: str, key: str | None = None) -> None:
        """Tăng bộ đếm RPD (gọi bên trong _key_lock). Đánh dấu cạn key nếu key chạm ngưỡng RPD/key."""
        self._rpd_counts[model] = self._rpd_counts.get(model, 0) + 1
        if key:
            pair = (key, model)
            used_on_key = self._key_rpd_counts.get(pair, 0) + 1
            self._key_rpd_counts[pair] = used_on_key
            if used_on_key >= get_model_rpd_per_key(model, self.cfg):
                self._exhausted.add(pair)
        self._save_quota_state_unlocked()

    def _increment_rpd(self, model: str, key: str | None = None) -> None:
        """Tăng bộ đếm RPD khi request đã tiêu tốn 1 lượt gọi trên Google Gemini (kể cả 200 OK, 503, Timeout, JSON lỗi)."""
        with self._key_lock:
            self._check_and_reset_cycle()
            self._increment_rpd_unlocked(model, key)

    def _rpm_count(self, key: str, model: str | None = None) -> int:
        """Đếm request của key (hoặc cặp key, model) trong 60s gần nhất, dọn entry cũ. Gọi bên trong _key_lock."""
        cutoff = time.time() - _RPM_WINDOW_S
        if model is not None:
            pair = (key, model)
            if pair in self._model_rpm_tracker:
                self._model_rpm_tracker[pair] = [t for t in self._model_rpm_tracker[pair] if t > cutoff]
                return len(self._model_rpm_tracker[pair])
            return 0
        if key in self._rpm_tracker:
            self._rpm_tracker[key] = [t for t in self._rpm_tracker[key] if t > cutoff]
            return len(self._rpm_tracker[key])
        return 0

    def _earliest_ready_ts_unlocked(self, key: str, model: str, now: float) -> float:
        """Tính thời điểm sớm nhất mà cặp (key, model) được phép gọi request tiếp theo (đảm bảo không vượt RPM và cách nhau >= min_interval_s)."""
        min_interval = get_model_min_interval_s(model, self.cfg)
        rpm_limit = get_model_rpm_per_key(model, self.cfg)
        ready = max(
            now,
            self._key_cooldown.get(key, 0.0),
            self._next_allowed_ts.get((key, model), 0.0),
        )
        # Kiểm tra cửa sổ trượt 60s của (key, model)
        pair = (key, model)
        cutoff = now - _RPM_WINDOW_S
        hist = sorted(t for t in self._model_rpm_tracker.get(pair, []) if t > cutoff)
        self._model_rpm_tracker[pair] = hist
        if len(hist) >= rpm_limit:
            # Phải chờ cho tới khi request cũ thứ (len - rpm_limit) trôi qua cửa sổ 60s (+0.5s buffer an toàn)
            oldest_blocking = hist[len(hist) - rpm_limit]
            ready = max(ready, oldest_blocking + _RPM_WINDOW_S + 0.5)
        if hist and min_interval > 0:
            ready = max(ready, hist[-1] + min_interval)
        return ready

    def _set_cooldown(self, key: str) -> None:
        """Đặt cooldown cho key sau khi bị 429 RPM limit."""
        self._key_cooldown[key] = time.time() + _COOLDOWN_S

    def _next_key_client(self) -> tuple[str, any]:
        with self._key_lock:
            k = self._api_keys[self._key_index % len(self._api_keys)]
            self._key_index += 1
            return k, self._get_or_create_client(k)

    def _acquire_paced_key_client_with_ts(
        self, model: str, skip: set[str] | None = None
    ) -> tuple[str | None, any, float, float]:
        """Chọn API key TUẦN TỰ cho `model`: ưu tiên key sẵn sàng ngay từ con trỏ model,
        nếu mọi key còn lại đều đang chờ RPM/cooldown thì chọn key sẵn sàng sớm nhất.

        - Bỏ qua các cặp (key, model) đã hết RPD và các key trong `skip` (đã thử trong vòng hiện tại).
        - Đặt chỗ (reserve) thời điểm gọi để mỗi (key, model) không bao giờ vượt RPM / min_interval_s.
        Trả về (key, client, wait_seconds, scheduled_ts); nếu không còn key phù hợp, trả về (None, None, 0.0, 0.0).
        """
        with self._key_lock:
            self._check_and_reset_cycle()
            n = len(self._api_keys)
            if n == 0:
                return None, None, 0.0, 0.0
            now = time.time()
            start = self._model_cursor.get(model, 0) % n
            chosen_idx: int | None = None
            chosen_ready_ts: float = 0.0

            for off in range(n):
                idx = (start + off) % n
                cand = self._api_keys[idx]
                if (cand, model) in self._exhausted:
                    continue
                if skip and cand in skip:
                    continue
                ready_ts = self._earliest_ready_ts_unlocked(cand, model, now)
                if ready_ts <= now:
                    chosen_idx = idx
                    chosen_ready_ts = now
                    break
                if chosen_idx is None or ready_ts < chosen_ready_ts:
                    chosen_idx = idx
                    chosen_ready_ts = ready_ts

            if chosen_idx is None:
                return None, None, 0.0, 0.0

            k = self._api_keys[chosen_idx]
            # Con trỏ chỉ sang key kế tiếp -> lượt gọi sau (của bước này hoặc bước tiếp theo) bắt đầu từ đó
            self._model_cursor[model] = (chosen_idx + 1) % n

            min_interval = get_model_min_interval_s(model, self.cfg)
            scheduled_ts = max(now, chosen_ready_ts)
            wait_s = max(0.0, scheduled_ts - now)

            # Đặt chỗ trước (pre-reserve) timestamp để không bao giờ vượt RPM của (key, model)
            self._rpm_tracker.setdefault(k, []).append(scheduled_ts)
            self._model_rpm_tracker.setdefault((k, model), []).append(scheduled_ts)
            if min_interval > 0:
                self._next_allowed_ts[(k, model)] = max(
                    self._next_allowed_ts.get((k, model), 0.0),
                    scheduled_ts + min_interval,
                )
            self._save_quota_state_unlocked()

            return k, self._get_or_create_client(k), wait_s, scheduled_ts

    def _acquire_paced_key_client(
        self, model: str, skip: set[str] | None = None
    ) -> tuple[str | None, any, float]:
        """Wrapper giữ nguyên chữ ký 3-tuple (key, client, wait_seconds)."""
        k, client, wait_s, _ = self._acquire_paced_key_client_with_ts(model, skip=skip)
        return k, client, wait_s

    def _update_call_timestamp(self, key: str, model: str, reserved_ts: float, actual_ts: float) -> None:
        """Cập nhật timestamp thực tế khi request bắt đầu chạy (tránh trôi RPM do chờ semaphore/sleep)."""
        if actual_ts <= reserved_ts:
            return
        with self._key_lock:
            for tracker in (
                self._rpm_tracker.get(key),
                self._model_rpm_tracker.get((key, model)),
            ):
                if tracker:
                    try:
                        idx = tracker.index(reserved_ts)
                        tracker[idx] = actual_ts
                    except ValueError:
                        tracker.append(actual_ts)
                    tracker.sort()
            min_interval = get_model_min_interval_s(model, self.cfg)
            if min_interval > 0:
                cur_allowed = self._next_allowed_ts.get((key, model), 0.0)
                if actual_ts + min_interval > cur_allowed:
                    self._next_allowed_ts[(key, model)] = actual_ts + min_interval
            self._save_quota_state_unlocked()

    def _release_reserved_slot(self, key: str, model: str, reserved_ts: float) -> None:
        """Hoàn trả slot đã đặt chỗ nếu key bị đánh dấu cạn/cooldown trong lúc luồng đang ngủ chờ."""
        with self._key_lock:
            for tracker in (
                self._rpm_tracker.get(key),
                self._model_rpm_tracker.get((key, model)),
            ):
                if tracker:
                    try:
                        tracker.remove(reserved_ts)
                    except ValueError:
                        pass
            min_interval = get_model_min_interval_s(model, self.cfg)
            pair = (key, model)
            if min_interval > 0 and pair in self._next_allowed_ts:
                cur_allowed = self._next_allowed_ts[pair]
                if cur_allowed <= reserved_ts + min_interval + 1e-3:
                    hist = self._model_rpm_tracker.get(pair) or []
                    reverted = max(hist) + min_interval if hist else reserved_ts
                    if reverted <= time.time():
                        self._next_allowed_ts.pop(pair, None)
                    else:
                        self._next_allowed_ts[pair] = reverted
            self._save_quota_state_unlocked()

    def _has_available_key(self, model: str) -> bool:
        with self._key_lock:
            self._check_and_reset_cycle()
            return any((k, model) not in self._exhausted for k in self._api_keys)

    def _get_available_key_client(self, model: str, *, wait: bool = True) -> tuple[str, any] | tuple[None, None]:
        k, client, wait_s = self._acquire_paced_key_client(model)
        if k is None:
            return None, None
        if wait and wait_s > 0:
            time.sleep(wait_s)
        return k, client

    def _key_label(self, key: str) -> str:
        """Nhãn hiển thị an toàn của API key trong log (chỉ số thứ tự và 4 ký tự cuối)."""
        try:
            idx = self._api_keys.index(key) + 1
        except ValueError:
            idx = 1
        tail = key[-4:] if key and len(key) > 6 else "***"
        return f"Key #{idx} (...{tail})"

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

    def _compute_call_timeout_s(
        self, model_name: str, system: str, prompt: str, timeout_retries: int = 0
    ) -> float | None:
        """Tính timeout cho 1 lượt gọi API; trả về None nếu cấu hình tắt timeout (chờ tới khi model trả kết quả)."""
        base = get_model_timeout_s(model_name, self.cfg)
        if base is None:
            return None
        total_chars = len(system or "") + len(prompt or "")
        eff = base
        if total_chars > 25_000:
            eff += min(120.0, ((total_chars - 25_000) / 10_000.0) * 30.0)
        if timeout_retries > 0:
            cap = max(900.0, eff)
            for _ in range(timeout_retries):
                eff = min(cap, max(eff * 1.35, eff + min(60.0, base)))
        return round(max(1.0, eff), 1)

    def _call_once(self, model_name, step, system, prompt, schema, client=None, *, timeout: float | None = _UNSET):
        if client is None:
            _, client = self._next_key_client()
        if timeout is not _UNSET:
            eff_timeout = timeout
        else:
            tls_val = getattr(self._tls_timeout, "value", _UNSET)
            eff_timeout = tls_val if tls_val is not _UNSET else get_model_timeout_s(model_name, self.cfg)
        resp = client.interactions.create(
            model=model_name,
            input=prompt,
            system_instruction=system,
            response_format={"type": "text", "mime_type": "application/json", "schema": schema},
            generation_config=self._gen_config(step, model_name),
            store=False,
            timeout=float(eff_timeout) if eff_timeout is not None else None,
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

    def _reserve_payload_wait(self, payload_sig: str, min_wait_s: float = 0.0) -> float:
        """Giãn nhịp tối thiểu giữa các request có nội dung giống hệt nhau để tránh vượt QPS payload trùng lặp."""
        if self.backoff_max >= 2.0:
            min_gap = min(self.backoff_max, max(3.5, self.backoff_base * 1.5))
        elif self.backoff_max > 0.5:
            min_gap = min(self.backoff_max, max(0.05, self.backoff_base * 1.5))
        elif self.backoff_max > 0.1:
            min_gap = min(1.2, self.backoff_max)
        else:
            min_gap = 0.0
        with self._key_lock:
            now = time.time()
            if len(self._payload_next_ts) > 64:
                cutoff = now - 120.0
                self._payload_next_ts = {k: v for k, v in self._payload_next_ts.items() if v > cutoff}
            p_ready = self._payload_next_ts.get(payload_sig, 0.0)
            earliest_start = max(now + max(0.0, min_wait_s), p_ready)
            if min_gap > 0.0:
                self._payload_next_ts[payload_sig] = earliest_start + min_gap
            return max(0.0, earliest_start - now)

    def _reserve_gateway_wait(self, min_wait_s: float = 0.0) -> float:
        """Giãn nhịp toàn cục giữa các luồng và tôn trọng cooldown cổng API khi vừa gặp 429 QPS."""
        if self.backoff_max <= 0.5:
            return 0.0
        with self._key_lock:
            now = time.time()
            base_ts = now + max(0.0, min_wait_s)
            earliest = max(base_ts, self._global_qps_next_ts)
            dispatch_gap = self.global_min_dispatch_interval_s if len(self._api_keys) > 1 else 0.0
            if dispatch_gap > 0.0:
                cutoff = now - max(10.0, dispatch_gap * 2.0)
                slots = sorted(t for t in self._dispatch_slots if t > cutoff)
                t_cand = earliest
                # Giới hạn jitter tỉ lệ theo dispatch_gap và cộng ngay trong lúc trượt slot
                # để luôn giữ khoảng cách >= dispatch_gap với mọi slot đã đặt kế tiếp.
                jitter_cap = min(0.25, max(0.02, dispatch_gap * 0.5))
                jitter_min = min(0.05, jitter_cap * 0.5)
                for s in slots:
                    if t_cand + dispatch_gap <= s:
                        break
                    if t_cand < s + dispatch_gap:
                        t_cand = s + dispatch_gap + random.uniform(jitter_min, jitter_cap)
                slots.append(t_cand)
                self._dispatch_slots = slots
                earliest = t_cand
            return max(0.0, earliest - now)

    def _schedule_payload_qps_backoff(self, payload_sig: str, qps_hits: int) -> float:
        """Đặt thời gian lùi lũy thừa theo payload (kèm hạ nhiệt cổng chung) khi gặp lỗi 429 QPS."""
        if self.backoff_base <= 0:
            qps_backoff = 0.0
        elif self.backoff_max <= 0.5:
            qps_backoff = min(self.backoff_max, self.backoff_base * (2 ** min(max(0, qps_hits - 1), 3)))
        elif self.backoff_max < 2.0:
            raw_exp = self.backoff_base * (2 ** min(max(0, qps_hits), 4))
            qps_backoff = min(self.backoff_max, raw_exp)
        else:
            raw_exp = max(4.0, self.backoff_base * (2 ** min(max(0, qps_hits), 4)))
            qps_backoff = min(30.0, raw_exp) + random.uniform(0.2, 1.2)
        if qps_backoff > 0.0:
            with self._key_lock:
                now = time.time()
                target = now + qps_backoff
                if target > self._payload_next_ts.get(payload_sig, 0.0):
                    self._payload_next_ts[payload_sig] = target
                if self.backoff_max > 0.5 and self.qps_global_cooldown_s > 0.0:
                    global_cd = min(self.qps_global_cooldown_s, max(4.0, qps_backoff))
                    g_target = now + global_cd
                    if g_target > self._global_qps_next_ts:
                        self._global_qps_next_ts = g_target
        return qps_backoff

    def _has_non_outage_fallback_unlocked(self, cur_model: str, now: float) -> bool:
        """Kiểm tra có model dự phòng nào còn key khả dụng và không đang hạ nhiệt bão 503 không (gọi trong _key_lock)."""
        for m in self.models_chain:
            if m == cur_model:
                continue
            if self._model_outage_until.get(m, 0.0) > now:
                continue
            if any((k, m) not in self._exhausted for k in self._api_keys):
                return True
        return False

    def _ordered_models_for_call(self) -> list[str]:
        """Ưu tiên các model không đang trong giai đoạn hạ nhiệt bão 503; model đang hạ nhiệt dồn xuống cuối làm phương án cuối."""
        with self._key_lock:
            if self.backoff_max <= 0.5 or not self._model_outage_until:
                return list(self.models_chain)
            now = time.time()
            self._model_outage_until = {m: ts for m, ts in self._model_outage_until.items() if ts > now}
            if not self._model_outage_until:
                return list(self.models_chain)
            healthy = [m for m in self.models_chain if m not in self._model_outage_until]
            cooling = sorted(
                (m for m in self.models_chain if m in self._model_outage_until),
                key=lambda m: self._model_outage_until[m],
            )
            return healthy + cooling

    def _raise_cancelled_log(
        self,
        record: dict,
        log_dir: Path | None,
        log_path: Path | None,
        t_start: float,
        *,
        in_flight: bool,
        cur_key: str,
        cur_model: str,
        scheduled_ts: float,
    ) -> None:
        if in_flight:
            self._increment_rpd(cur_model, cur_key)
        else:
            self._release_reserved_slot(cur_key, cur_model, scheduled_ts)
        record.update(
            in_progress=False,
            phase="done",
            status="cancelled",
            action="cancelled_in_flight" if in_flight else "cancelled_while_waiting",
            duration_s=round(time.time() - t_start, 2),
            error="Đã dừng theo yêu cầu của người dùng.",
        )
        write_log(log_dir, record, self._api_keys, log_path=log_path)
        raise LLMCancelledError("Đã dừng chạy AI theo yêu cầu của người dùng.")

    def _call_once_cancellable(
        self,
        cancel_ev: threading.Event,
        cur_model: str,
        step: str,
        system: str,
        prompt: str,
        schema: dict,
        cur_client,
        cur_key: str,
        scheduled_ts: float,
        t_init: float,
        t0: float,
        record: dict,
        log_dir: Path | None,
        log_path: Path | None,
    ) -> tuple[str, dict, object]:
        sem = self._semaphore
        acquired_sem = False
        while not acquired_sem:
            if cancel_ev.is_set():
                self._raise_cancelled_log(
                    record, log_dir, log_path, t_init,
                    in_flight=False, cur_key=cur_key, cur_model=cur_model, scheduled_ts=scheduled_ts,
                )
            acquired_sem = sem.acquire(timeout=0.1)
        try:
            if cancel_ev.is_set():
                self._raise_cancelled_log(
                    record, log_dir, log_path, t_init,
                    in_flight=False, cur_key=cur_key, cur_model=cur_model, scheduled_ts=scheduled_ts,
                )
            t_call = time.time()
            self._update_call_timestamp(cur_key, cur_model, scheduled_ts, t_call)
            res_box: list[tuple[str, dict, object]] = []
            exc_box: list[BaseException] = []
            call_done = threading.Event()
            tls_timeout = getattr(self._tls_timeout, "value", _UNSET)

            def _run_call_once():
                prev_t = getattr(self._tls_timeout, "value", _UNSET)
                self._tls_timeout.value = tls_timeout
                try:
                    res_box.append(
                        self._call_once(cur_model, step, system, prompt, schema, client=cur_client)
                    )
                except BaseException as call_exc:  # noqa: BLE001
                    exc_box.append(call_exc)
                finally:
                    self._tls_timeout.value = prev_t
                    call_done.set()

            threading.Thread(target=_run_call_once, name=f"tomtat_call_{step}", daemon=True).start()
            while not call_done.wait(timeout=0.1):
                if cancel_ev.is_set():
                    self._raise_cancelled_log(
                        record, log_dir, log_path, t0,
                        in_flight=True, cur_key=cur_key, cur_model=cur_model, scheduled_ts=scheduled_ts,
                    )
            if cancel_ev.is_set():
                self._raise_cancelled_log(
                    record, log_dir, log_path, t0,
                    in_flight=True, cur_key=cur_key, cur_model=cur_model, scheduled_ts=scheduled_ts,
                )
            if exc_box:
                raise exc_box[0]
            return res_box[0]
        finally:
            if acquired_sem:
                sem.release()

    def generate_json(self, step, system, prompt, schema, *, log_dir=None, attempt=0, context=None):
        """Gọi model theo chiến lược TUẦN TỰ:

        - Model ưu tiên theo models_chain (4 Hydrogen -> 3.8 -> 3.6 -> ...).
        - Với mỗi model, gọi lần lượt từ con trỏ hiện tại (key kế tiếp sau key thành công gần nhất)
          tới key cuối, rồi quay vòng lại key #1, cho tới khi thành công.
        - 429 RPD/RPM -> đủ 3 lần trên cùng (key, model) mới đánh dấu hết RPD trong chu kỳ ngày.
        - 429 QPS payload trùng lặp -> lỗi tạm thời toàn cục, KHÔNG tính strike RPD và KHÔNG khóa key; chỉ lùi chờ theo payload.
        - 503 / timeout / 5xx / JSON lỗi -> chuyển sang key kế tiếp, vòng sau thử lại key này.
        - Khi mọi key đều hết RPD cho model -> chuyển sang model kế tiếp.
        """
        self.sync_keys()
        self.sync_config()
        cancel_ev = get_active_cancel_event()
        check_not_cancelled(cancel_ev)
        last_exc: BaseException | None = None
        net_try = 0
        global_qps_hits = 0
        payload_sig = hashlib.sha256(
            f"{step}\0{system or ''}\0{prompt or ''}".encode("utf-8", errors="replace")
        ).hexdigest()[:24]

        for cur_model in self._ordered_models_for_call():
            check_not_cancelled(cancel_ev)
            if not self._has_available_key(cur_model):
                continue  # toàn bộ key đã hết RPD cho model này -> model kế tiếp

            round_no = 1
            tried_this_round: set[str] = set()
            skip_model = False
            qps_streak = 0
            timeouts_on_model = 0
            transient_on_model = 0

            while not skip_model:
                check_not_cancelled(cancel_ev)
                cur_key, cur_client, wait_s, scheduled_ts = self._acquire_paced_key_client_with_ts(
                    cur_model, skip=tried_this_round
                )
                if cur_key is None:
                    if not self._has_available_key(cur_model):
                        break  # mọi key đã hết RPD (429) -> model kế tiếp
                    # Đã quét hết 1 vòng pool mà chưa thành công (503 / lỗi tạm thời) -> quay lại từ đầu
                    if self.max_rounds_per_model and round_no >= self.max_rounds_per_model:
                        break  # tạm hạ xuống model kế tiếp cho lượt gọi này (không đánh dấu hết RPD)
                    round_no += 1
                    tried_this_round.clear()
                    sleep_with_cancel(max(0.0, min(self.backoff_max, self.backoff_base)), cancel_ev)
                    continue

                payload_wait_s = self._reserve_payload_wait(payload_sig, min_wait_s=wait_s)
                gateway_wait_s = self._reserve_gateway_wait(min_wait_s=max(wait_s, payload_wait_s))
                eff_wait_s = max(wait_s, payload_wait_s, gateway_wait_s)

                tried_this_round.add(cur_key)
                net_try += 1
                key_lbl = self._key_label(cur_key)
                call_timeout = self._compute_call_timeout_s(
                    cur_model, system, prompt, timeout_retries=timeouts_on_model
                )
                call_prompt = (
                    prompt
                    if (global_qps_hits == 0 or self.backoff_max <= 0.5)
                    else f"{prompt}\n\n[Phiên thử lại #{net_try} · {cur_model}]"
                )
                t_init = time.time()
                record = {
                    "step": step,
                    "attempt": attempt,
                    "net_try": net_try,
                    "model": cur_model,
                    "key_label": key_lbl,
                    "round": round_no,
                    "timeout_s": call_timeout,
                    "started_at": datetime.now().isoformat(timespec="seconds"),
                    "started_epoch": round(t_init, 3),
                    "in_progress": True,
                    "phase": "waiting_rpm" if eff_wait_s >= 0.5 else "calling_api",
                    "wait_s": round(eff_wait_s, 1) if eff_wait_s >= 0.5 else 0,
                    "status": "waiting" if eff_wait_s >= 0.5 else "running",
                    "generation_config": self._gen_config(step, cur_model),
                    "system": system,
                    "prompt": prompt,
                    "schema": schema,
                }
                log_path = write_log(log_dir, record, self._api_keys)

                if eff_wait_s > 0:
                    with self._key_lock:
                        cooldown_before_wait = self._key_cooldown.get(cur_key, 0.0)
                        outage_before_wait = self._model_outage_until.get(cur_model, 0.0)
                    if cancel_ev is not None:
                        if cancel_ev.wait(timeout=eff_wait_s + 0.05):
                            self._raise_cancelled_log(
                                record, log_dir, log_path, t_init,
                                in_flight=False, cur_key=cur_key, cur_model=cur_model, scheduled_ts=scheduled_ts,
                            )
                    else:
                        time.sleep(eff_wait_s + 0.05)
                    # Trong lúc ngủ chờ, luồng khác có thể đã đánh dấu key cạn RPD, phạt cooldown mới hoặc model vào bão 503
                    with self._key_lock:
                        now_after_wait = time.time()
                        cur_cd = self._key_cooldown.get(cur_key, 0.0)
                        cur_outage = self._model_outage_until.get(cur_model, 0.0)
                        entered_outage = (
                            self.backoff_max > 0.5
                            and cur_outage > outage_before_wait
                            and cur_outage > now_after_wait
                            and self._has_non_outage_fallback_unlocked(cur_model, now_after_wait)
                        )
                        became_unusable = (
                            (cur_key, cur_model) in self._exhausted
                            or (cur_cd > cooldown_before_wait and cur_cd > now_after_wait)
                        )
                    if entered_outage or became_unusable:
                        self._release_reserved_slot(cur_key, cur_model, scheduled_ts)
                        record.update(
                            in_progress=False,
                            phase="done",
                            status="skipped",
                            action="fallback_model_outage" if entered_outage else "requeue_after_wait",
                        )
                        write_log(log_dir, record, self._api_keys, log_path=log_path)
                        if entered_outage:
                            break
                        continue

                if cancel_ev is not None and cancel_ev.is_set():
                    self._raise_cancelled_log(
                        record, log_dir, log_path, t_init,
                        in_flight=False, cur_key=cur_key, cur_model=cur_model, scheduled_ts=scheduled_ts,
                    )

                t0 = time.time()
                record.update(
                    started_at=datetime.now().isoformat(timespec="seconds"),
                    started_epoch=round(t0, 3),
                    in_progress=True,
                    phase="calling_api",
                    status="running",
                )
                log_path = write_log(log_dir, record, self._api_keys, log_path=log_path)

                try:
                    prev_tls_timeout = getattr(self._tls_timeout, "value", _UNSET)
                    self._tls_timeout.value = call_timeout
                    try:
                        if cancel_ev is None:
                            sem = self._semaphore
                            with sem:
                                t_call = time.time()
                                self._update_call_timestamp(cur_key, cur_model, scheduled_ts, t_call)
                                text, usage, status = self._call_once(cur_model, step, system, call_prompt, schema, client=cur_client)
                        else:
                            text, usage, status = self._call_once_cancellable(
                                cancel_ev, cur_model, step, system, call_prompt, schema, cur_client,
                                cur_key, scheduled_ts, t_init, t0, record, log_dir, log_path,
                            )
                    finally:
                        self._tls_timeout.value = prev_tls_timeout
                    qps_streak = 0
                    with self._key_lock:
                        self._idle_429_strikes.pop((cur_key, cur_model), None)
                        self._model_503_streak.pop(cur_model, None)
                        self._model_outage_until.pop(cur_model, None)
                    self._increment_rpd(cur_model, cur_key)
                    dur = time.time() - t0
                    record.update(
                        in_progress=False,
                        phase="done",
                        duration_s=round(dur, 2),
                        response_text=text,
                        usage=usage,
                        status=str(status),
                        api_status=str(status) if status is not None else "",
                    )
                    if str(status or "").lower() == "incomplete" or not (text or "").strip():
                        thought_tok = (usage or {}).get("total_thought_tokens") or 0
                        out_tok = (usage or {}).get("total_output_tokens") or 0
                        max_tok = (record.get("generation_config") or {}).get("max_output_tokens")
                        if str(status or "").lower() == "incomplete" or (
                            max_tok and (thought_tok + out_tok) >= int(max_tok)
                        ):
                            err_msg = (
                                f"Phản hồi bị cắt do chạm trần max_output_tokens={max_tok} "
                                f"(api_status={status}, think={thought_tok}, out={out_tok}); "
                                f"hãy tăng max_output_tokens hoặc giảm thinking_level"
                            )
                        else:
                            err_msg = (
                                f"Phản hồi rỗng từ Gemini (api_status={status}, "
                                f"think={thought_tok}, out={out_tok})"
                            )
                        record.update(status="error", error=err_msg)
                        write_log(log_dir, record, self._api_keys, log_path=log_path)
                        last_exc = LLMError(err_msg)
                        continue
                    data = _parse_json(text)
                    record["parsed_ok"] = True
                    write_log(log_dir, record, self._api_keys, log_path=log_path)
                    return LLMResult(data=data, text=text, usage=usage, duration_s=dur, model=cur_model)
                except LLMCancelledError:
                    raise
                except json.JSONDecodeError as exc:
                    qps_streak = 0
                    record.update(
                        in_progress=False,
                        phase="done",
                        status="error",
                        duration_s=round(time.time() - t0, 2),
                        error=f"JSON không hợp lệ: {exc}",
                    )
                    write_log(log_dir, record, self._api_keys, log_path=log_path)
                    last_exc = exc
                    continue  # thử key kế tiếp
                except Exception as exc:  # noqa: BLE001
                    err_str = _redact(f"{type(exc).__name__}: {exc}", self._api_keys)
                    st_code = _status_of(exc)
                    # Lỗi 503 (high demand), 500, 502, 504 hoặc Timeout vẫn bị Google tính 1 RPD
                    if _error_consumes_rpd(err_str, st_code):
                        self._increment_rpd(cur_model, cur_key)
                    record.update(
                        in_progress=False,
                        phase="done",
                        status="error",
                        duration_s=round(time.time() - t0, 2),
                        error=err_str,
                    )
                    last_exc = exc
                    err_lower = err_str.lower()
                    is_quota_429 = (
                        st_code == 429
                        or "resource_exhausted" in err_lower
                        or "resource has been exhausted" in err_lower
                        or "exceeded a quota" in err_lower
                        or "quota exceeded" in err_lower
                        or "too_many_requests" in err_lower
                    )
                    if is_quota_429:
                        if _is_payload_qps_429(err_lower):
                            # Giới hạn QPS theo payload giống nhau trên cổng Google: KHÔNG tính strike RPD, KHÔNG khóa key
                            qps_streak += 1
                            global_qps_hits += 1
                            self._schedule_payload_qps_backoff(payload_sig, global_qps_hits)
                            record["action"] = "backoff_payload_qps"
                            record["qps_retry"] = qps_streak
                            write_log(log_dir, record, self._api_keys, log_path=log_path)
                            if qps_streak >= 3:
                                skip_model = True
                            continue
                        qps_streak = 0
                        is_explicit_daily_quota = any(tok in err_lower for tok in (
                            "per day", "per_day", "requests per day", "requestsperday",
                            "daily", "rpd", "limit: 0",
                        ))
                        is_explicit_rpm_quota = any(tok in err_lower for tok in (
                            "per minute", "per_minute", "requests per minute", "requestsperminute",
                            "tokensperminute", "rpm", "tpm",
                        ))
                        # Với mỗi cặp (key, model), phải trả về mã 429 đủ 3 lần mới đánh dấu hết hạn mức (exhausted)
                        with self._key_lock:
                            self._set_cooldown(cur_key)
                            strikes = self._idle_429_strikes.get((cur_key, cur_model), 0) + 1
                            self._idle_429_strikes[(cur_key, cur_model)] = strikes
                            exhausted_now = strikes >= 3
                            if exhausted_now:
                                self._check_and_reset_cycle()
                                self._exhausted.add((cur_key, cur_model))
                                self._idle_429_strikes.pop((cur_key, cur_model), None)
                            self._save_quota_state_unlocked()
                        if exhausted_now:
                            record["action"] = "mark_exhausted_rpd"
                        elif is_explicit_rpm_quota:
                            record["action"] = "cooldown_rpm"
                        elif is_explicit_daily_quota:
                            record["action"] = "cooldown_429_strike"
                        else:
                            record["action"] = "cooldown_project_quota"
                        record["strikes_429"] = strikes
                        write_log(log_dir, record, self._api_keys, log_path=log_path)
                        continue
                    qps_streak = 0
                    if st_code in (401, 403) or "unauthenticated" in err_lower or "not valid" in err_lower:
                        # Key lỗi xác thực -> loại khỏi mọi model
                        for m in self.models_chain:
                            self._mark_exhausted(cur_key, m)
                        record["action"] = "invalid_key"
                        write_log(log_dir, record, self._api_keys, log_path=log_path)
                        continue
                    if st_code == 404:
                        record["action"] = "model_not_found"
                        write_log(log_dir, record, self._api_keys, log_path=log_path)
                        skip_model = True
                        continue
                    if _is_timeout_error(exc, err_lower, st_code):
                        self._evict_client(cur_key)
                        timeouts_on_model += 1
                        record["timeout_retry"] = timeouts_on_model
                        if self.max_timeouts_per_model and timeouts_on_model >= self.max_timeouts_per_model:
                            record["action"] = "fallback_timeout"
                            write_log(log_dir, record, self._api_keys, log_path=log_path)
                            skip_model = True
                            continue
                        record["action"] = "next_key_timeout"
                        write_log(log_dir, record, self._api_keys, log_path=log_path)
                        continue
                    if _is_connection_error(exc, err_lower):
                        self._evict_client(cur_key)
                    if is_transient(exc) or st_code is None:
                        # 503 high demand / 5xx / lỗi kết nối tạm: giãn nhịp payload, ngắt mạch khi bão 503 để bảo toàn RPD
                        transient_on_model += 1
                        record["transient_retry"] = transient_on_model
                        hit_outage_breaker = False
                        if self.backoff_max > 0.5:
                            raw_tr = max(0.0, self.backoff_base * (1.5 ** min(max(0, transient_on_model - 1), 3)))
                            tr_jitter = random.uniform(0.05, min(0.6, self.backoff_max * 0.25))
                            tr_wait = min(self.backoff_max, raw_tr + tr_jitter)
                            with self._key_lock:
                                now_tr = time.time()
                                if now_tr + tr_wait > self._payload_next_ts.get(payload_sig, 0.0):
                                    self._payload_next_ts[payload_sig] = now_tr + tr_wait
                                streak = self._model_503_streak.get(cur_model, 0) + 1
                                self._model_503_streak[cur_model] = streak
                                if self.model_outage_threshold and streak >= self.model_outage_threshold:
                                    if self.model_outage_cooldown_s > 0.0:
                                        self._model_outage_until[cur_model] = now_tr + self.model_outage_cooldown_s
                                    self._model_503_streak[cur_model] = 0
                                    if self._has_non_outage_fallback_unlocked(cur_model, now_tr):
                                        hit_outage_breaker = True
                                elif (
                                    self._model_outage_until.get(cur_model, 0.0) > now_tr
                                    and self._has_non_outage_fallback_unlocked(cur_model, now_tr)
                                ):
                                    hit_outage_breaker = True
                        if hit_outage_breaker:
                            record["action"] = "fallback_model_outage"
                            write_log(log_dir, record, self._api_keys, log_path=log_path)
                            skip_model = True
                            continue
                        if self.max_transient_per_model and transient_on_model >= self.max_transient_per_model:
                            record["action"] = "fallback_transient"
                            write_log(log_dir, record, self._api_keys, log_path=log_path)
                            skip_model = True
                            continue
                        record["action"] = "next_key"
                        write_log(log_dir, record, self._api_keys, log_path=log_path)
                        continue
                    # Lỗi không thể thử lại (vd 400) -> sẽ lặp lại trên mọi key, chuyển sang model kế tiếp
                    record["action"] = "next_model"
                    write_log(log_dir, record, self._api_keys, log_path=log_path)
                    skip_model = True

        if last_exc is None:
            raise LLMError(
                f"Gọi Gemini thất bại ở bước {step}: Tất cả {len(self._api_keys)} API key đều đã hết RPD (429) hoặc liên tục quá tải (503) "
                f"trên mọi model ({', '.join(self.models_chain)}). Vui lòng thử lại sau hoặc chờ tới 15:00 giờ VN (reset hạn mức)."
            )
        msg = _redact(f"{type(last_exc).__name__}: {last_exc}", self._api_keys)
        st = _status_of(last_exc)
        hint = ""
        if st in (401, 403):
            hint = " (kiểm tra GEMINI_API_KEY và quyền truy cập model)"
        elif st == 402:
            hint = " (Hết số dư tiền nạp trước trên Google AI Studio; vui lòng nạp thêm tiền tại https://ai.studio/projects hoặc https://ai.google.dev/gemini-api/docs/billing#prepay)"
        elif st == 404:
            hint = f" (model '{self.model}' không tồn tại hoặc tài khoản chưa được dùng; đổi gemini.model trong config.yaml)"
        elif st == 429:
            if _is_payload_qps_429(msg.lower()):
                hint = " (Google đang giới hạn QPS do nội dung prompt giống nhau được gửi liên tiếp; hãy đợi vài giây rồi thử lại — RPD các key KHÔNG bị khóa)"
            else:
                hint = " (mọi key đều đã hết RPD trên mọi model; chờ tới 15:00 giờ VN (reset hạn mức) hoặc thêm key mới)"
        elif _is_timeout_error(last_exc, msg.lower(), st):
            hint = (
                " (mọi model đã thử đều vượt quá thời gian chờ phản hồi; "
                "hãy kiểm tra kết nối mạng hoặc tăng timeout_s / timeout_s_by_model trong config.yaml)"
            )
        raise LLMError(f"Gọi Gemini thất bại ở bước {step}: {msg}{hint}")
