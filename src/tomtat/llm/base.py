"""Lớp cơ sở, kiểu dữ liệu và tiện ích dùng chung cho tất cả LLM client."""
from __future__ import annotations

import contextlib
import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


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


TRANSIENT_STATUS = {408, 429, 500, 502, 503, 504}
_RPM_WINDOW_S = 60.0        # cửa sổ trượt đếm RPM (giây)
_COOLDOWN_S = 30             # thời gian cooldown sau khi key bị 429 (giây)
_QUOTA_FILE_LOCK = threading.Lock()

DEFAULT_RPD_LIMITS: dict[str, int] = {
    "gemini-4-hydrogen": 20,
    "gemini-3.8-flash": 20,
    "gemini-3.6-flash": 20,
    "gemini-3.5-flash-lite": 500,
    "gemini-3.1-flash-lite": 500,
}

DEFAULT_RPM_LIMITS: dict[str, int] = {
    "gemini-4-hydrogen": 5,
    "gemini-3.8-flash": 5,
    "gemini-3.6-flash": 5,
    "gemini-3.5-flash-lite": 15,
    "gemini-3.1-flash-lite": 15,
}

DEFAULT_MIN_INTERVAL_S: dict[str, float] = {
    "gemini-4-hydrogen": 13.0,
    "gemini-3.8-flash": 13.0,
    "gemini-3.6-flash": 13.0,
    "gemini-3.5-flash-lite": 4.5,
    "gemini-3.1-flash-lite": 4.5,
}

DEFAULT_TIMEOUT_S: dict[str, float] = {
    "gemini-4-hydrogen": 600.0,
    "gemini-4-argon": 600.0,
    "gemini-3.8-flash": 420.0,
    "gemini-3.6-flash": 360.0,
    "gemini-3.5-flash-lite": 180.0,
    "gemini-3.1-flash-lite": 180.0,
}


def get_model_rpd_per_key(model: str, cfg: dict | None = None) -> int:
    """Trả về hạn mức RPD (requests/ngày) trên mỗi API key của một model."""
    if cfg:
        custom = (cfg.get("gemini") or {}).get("rpd_limits") or {}
        if model in custom:
            try:
                return max(1, int(custom[model]))
            except (TypeError, ValueError):
                pass
    if model in DEFAULT_RPD_LIMITS:
        return DEFAULT_RPD_LIMITS[model]
    return 500 if "lite" in (model or "").lower() else 20


def get_model_rpm_per_key(model: str, cfg: dict | None = None) -> int:
    """Trả về hạn mức RPM (requests/phút) trên mỗi API key của một model."""
    if cfg:
        custom = (cfg.get("gemini") or {}).get("rpm_limits") or {}
        if model in custom:
            try:
                return max(1, int(custom[model]))
            except (TypeError, ValueError):
                pass
    if model in DEFAULT_RPM_LIMITS:
        return DEFAULT_RPM_LIMITS[model]
    return 15 if "lite" in (model or "").lower() else 5


def get_model_min_interval_s(model: str, cfg: dict | None = None) -> float:
    """Trả về khoảng cách tối thiểu (giây) giữa 2 lần gọi API trên cùng 1 key đối với một model."""
    if cfg:
        custom = (cfg.get("gemini") or {}).get("min_interval_s")
        if isinstance(custom, (int, float)) and not isinstance(custom, bool):
            return max(0.0, float(custom))
        if isinstance(custom, dict) and model in custom:
            try:
                return max(0.0, float(custom[model]))
            except (TypeError, ValueError):
                pass
    if model in DEFAULT_MIN_INTERVAL_S:
        return DEFAULT_MIN_INTERVAL_S[model]
    rpm = get_model_rpm_per_key(model, cfg)
    return round((60.0 / rpm) + 1.0, 2) if rpm <= 5 else round((60.0 / rpm) + 0.5, 2)


def _parse_timeout_value(val) -> tuple[bool, float | None]:
    """Chuẩn hóa giá trị timeout từ config: (được_cấu_hình, số_giây_hoặc_None_nếu_không_giới_hạn)."""
    if val is None or val is False:
        return True, None
    if isinstance(val, str):
        s = val.strip().lower()
        if s in ("", "none", "null", "off", "unlimited", "inf", "no"):
            return True, None
        try:
            num = float(s)
        except ValueError:
            return False, None
        return True, (num if num > 0 else None)
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        num = float(val)
        return True, (num if num > 0 else None)
    return False, None


def get_model_timeout_s(model: str, cfg: dict | None = None) -> float | None:
    """Trả về timeout (giây) cho một model, hoặc None nếu tắt client-side read timeout (chờ tới khi model chạy xong).

    Ưu tiên: timeout_s_by_model[model] -> timeout_s[model] -> timeout_s (nếu được cấu hình) -> None (không giới hạn).
    """
    g = (cfg.get("gemini") or {}) if cfg else {}
    by_model = g.get("timeout_s_by_model") or {}
    if isinstance(by_model, dict) and model in by_model:
        found, parsed = _parse_timeout_value(by_model[model])
        if found:
            return max(1.0, parsed) if parsed is not None else None
    if "timeout_s" in g:
        raw_timeout = g.get("timeout_s")
        if isinstance(raw_timeout, dict):
            if model in raw_timeout:
                found, parsed = _parse_timeout_value(raw_timeout[model])
                if found:
                    return max(1.0, parsed) if parsed is not None else None
        else:
            found, parsed = _parse_timeout_value(raw_timeout)
            if found:
                return max(1.0, parsed) if parsed is not None else None
    return None


def _error_consumes_rpd(err_str: str, status_code: int | None = None) -> bool:
    """Kiểm tra xem một lượt gọi API bị lỗi có bị Google tính là 1 RPD hay không.

    Trên Google Gemini API, các lỗi 503 (high demand / service unavailable), 500, 502, 504,
    Timeout (APITimeoutError), hoặc lỗi parse JSON sau khi nhận phản hồi đều đã đi qua
    cổng kiểm soát quota nên vẫn bị trừ 1 RPD. Chỉ các lỗi bị chặn ngay tại cổng
    (429 RateLimit, 401/403 Auth, 404 NotFound) là không tính thêm RPD mới.
    """
    if not err_str and status_code is None:
        return True
    if status_code is not None:
        if status_code in (500, 502, 503, 504, 408):
            return True
        if status_code in (401, 403, 404, 429):
            return False
    low = (err_str or "").lower()
    if any(k in low for k in (
        "429", "too_many_requests", "exceeded a quota", "quota exceeded",
        "resource has been exhausted", "unauthenticated", "not valid",
        "permission_denied", "not_found", "401", "403", "404",
    )):
        return False
    if any(k in low for k in (
        "503", "500", "502", "504", "high demand", "service_unavailable",
        "internalservererror", "overloaded", "timeout", "timed out", "json",
    )):
        return True
    return True


def _get_quota_cycle_start_local_prefix(cycle_date: str) -> str:
    """Tính tiền tố tên file log 'YYYYMMDD-HHMMSS' theo giờ máy cục bộ tại thời điểm bắt đầu chu kỳ quota (00:00 Pacific Time)."""
    try:
        from zoneinfo import ZoneInfo
        pt_tz = ZoneInfo("America/Los_Angeles")
        dt_pt = datetime.strptime(cycle_date, "%Y-%m-%d").replace(tzinfo=pt_tz)
        dt_local = dt_pt.astimezone()
        return dt_local.strftime("%Y%m%d-%H%M%S")
    except Exception:
        return f"{cycle_date.replace('-', '')}-150000"


_LOG_RPD_CACHE_LOCK = threading.Lock()
_LOG_RPD_CACHE: dict = {
    "cycle_date": "",
    "work_dir": "",
    "start_prefix": "",
    "seen_done_files": set(),
    "done_counts": {},
}


def _reconcile_rpd_from_logs(
    work_path: Path, cycle_date: str, min_prefix: str | None = None
) -> dict[str, int]:
    """Đối soát số lượt gọi API thực tế từ các file log trong chu kỳ ngày hiện tại (bao gồm cả lỗi 503, Timeout, JSON)."""
    work_dir_str = str(work_path.resolve()) if work_path.exists() else str(work_path)
    start_prefix = max(_get_quota_cycle_start_local_prefix(cycle_date), min_prefix or "")

    with _LOG_RPD_CACHE_LOCK:
        if (
            _LOG_RPD_CACHE["cycle_date"] != cycle_date
            or _LOG_RPD_CACHE["work_dir"] != work_dir_str
            or _LOG_RPD_CACHE["start_prefix"] != start_prefix
        ):
            _LOG_RPD_CACHE["cycle_date"] = cycle_date
            _LOG_RPD_CACHE["work_dir"] = work_dir_str
            _LOG_RPD_CACHE["start_prefix"] = start_prefix
            _LOG_RPD_CACHE["seen_done_files"] = set()
            _LOG_RPD_CACHE["done_counts"] = {}

        seen_done: set[str] = _LOG_RPD_CACHE["seen_done_files"]
        done_counts: dict[str, int] = _LOG_RPD_CACHE["done_counts"]
        in_progress_counts: dict[str, int] = {}

        if work_path.exists():
            for lp in work_path.glob("*/logs/*.json"):
                if lp.name < start_prefix:
                    continue
                key_path = f"{lp.parent.parent.name}/{lp.name}"
                if key_path in seen_done:
                    continue
                try:
                    d = json.loads(lp.read_text(encoding="utf-8"))
                except Exception:
                    continue
                model = d.get("model")
                if not model:
                    continue
                phase = d.get("phase") or ""
                in_prog = bool(d.get("in_progress", False))
                if in_prog:
                    # Nếu đã qua bước chờ RPM và đang thực sự gọi API thì đã tính 1 RPD
                    if phase == "calling_api":
                        in_progress_counts[model] = in_progress_counts.get(model, 0) + 1
                    continue
                seen_done.add(key_path)
                if d.get("status") == "skipped" or d.get("action") in ("cancelled_while_waiting", "requeue_after_wait"):
                    continue
                err = d.get("error") or ""
                if not err or _error_consumes_rpd(err):
                    done_counts[model] = done_counts.get(model, 0) + 1

        combined = dict(done_counts)
        for m, cnt in in_progress_counts.items():
            combined[m] = combined.get(m, 0) + cnt
        return combined


def _is_payload_qps_429(err_lower: str) -> bool:
    """Kiểm tra lỗi 429 giới hạn QPS theo nội dung payload trùng lặp (tạm thời toàn cục, không phải cạn RPD)."""
    low = (err_lower or "").lower()
    return any(tok in low for tok in (
        "query-per-second",
        "near-identical",
        "identical or near-identical",
        "qps limit",
    ))


def _heal_qps_exhausted_items(
    work_path: Path,
    cycle_date: str,
    exhausted_items: list[dict],
    *,
    min_prefix: str = "",
) -> tuple[list[dict], bool]:
    """Gỡ bỏ các mục (key, model) trong quota_exhausted.json cũ bị khóa oan do 429 QPS payload trùng lặp."""
    if not exhausted_items or not work_path.exists():
        return list(exhausted_items), False

    start_prefix = max(_get_quota_cycle_start_local_prefix(cycle_date), min_prefix or "")
    last_reason: dict[tuple[str, str], str] = {}
    for lp in sorted(work_path.glob("*/logs/*.json"), key=lambda p: p.name):
        if lp.name < start_prefix:
            continue
        try:
            d = json.loads(lp.read_text(encoding="utf-8"))
        except Exception:
            continue
        act = d.get("action") or ""
        if act not in ("mark_exhausted_rpd", "invalid_key"):
            continue
        model = str(d.get("model") or "")
        if not model:
            continue
        lbl = str(d.get("key_label") or "")
        if "(..." in lbl and lbl.endswith(")"):
            tail = lbl.rsplit("(...", 1)[-1][:-1]
        else:
            tail = lbl
        if not tail or tail == "***":
            continue
        err_str = str(d.get("error") or "")
        if act == "invalid_key" or not _is_payload_qps_429(err_str):
            last_reason[(tail, model)] = "real"
        else:
            last_reason[(tail, model)] = "qps"

    if not last_reason:
        return list(exhausted_items), False

    healed: list[dict] = []
    removed = False
    for item in exhausted_items:
        k = str(item.get("key") or "")
        m = str(item.get("model") or "")
        tail = k[-4:] if k and len(k) > 6 else "***"
        if tail != "***" and last_reason.get((tail, m)) == "qps":
            removed = True
            continue
        healed.append(item)
    return healed, removed


def get_rpd_status(cfg: dict, api_keys: list[str] | None = None) -> list[dict]:
    """Lấy thống kê RPD (Requests Per Day) của từng model trong chuỗi cấu hình.

    Mỗi phần tử có dạng:
    {
        "model": str,
        "used": int,
        "limit": int,
        "rpd_per_key": int,
        "key_count": int,
        "exhausted_keys": int,
        "display": "[Tên model]: [used]/[limit]",
    }
    """
    import os
    import tomtat.llm as _llm_pkg

    if api_keys is None:
        from ..config import get_api_keys
        api_keys = get_api_keys()
    key_count = max(1, len(api_keys))

    g = cfg.get("gemini") or {}
    primary = g.get("model") or "gemini-4-hydrogen"
    fallbacks = g.get("fallback_models") or []
    models_chain = [primary] + [m for m in fallbacks if m and m != primary]

    work_path = cfg.work_dir if hasattr(cfg, "work_dir") else Path((cfg.get("paths") or {}).get("work", "work"))
    quota_file = Path(work_path) / "quota_exhausted.json"
    today = _llm_pkg._get_quota_cycle_date()

    rpd_counts: dict[str, int] = {}
    exhausted_items: list[dict] = []
    file_payload: dict | None = None
    reset_prefix: str = ""

    with _QUOTA_FILE_LOCK:
        if quota_file.exists():
            try:
                data = json.loads(quota_file.read_text(encoding="utf-8"))
                if data.get("date") == today:
                    file_payload = data
                    reset_prefix = str(data.get("reset_prefix") or "")
                    raw_counts = data.get("rpd_counts") or {}
                    for m_name, cnt in raw_counts.items():
                        if m_name and isinstance(cnt, (int, float)):
                            rpd_counts[str(m_name)] = max(0, int(cnt))
                    exhausted_items = data.get("exhausted") or []
            except Exception:
                pass

    # Đối soát bổ sung với các file log thực tế trong ngày (bắt trọn cả các lượt lỗi 503, Timeout, JSON)
    log_counts = _reconcile_rpd_from_logs(Path(work_path), today, min_prefix=reset_prefix)
    updated_from_logs = False
    for m_name, l_cnt in log_counts.items():
        if l_cnt > rpd_counts.get(m_name, 0):
            rpd_counts[m_name] = l_cnt
            updated_from_logs = True

    schema_v = int(file_payload.get("schema_version") or 1) if isinstance(file_payload, dict) else 2
    healed_qps = False
    if isinstance(file_payload, dict) and schema_v < 2:
        exhausted_items, healed_qps = _heal_qps_exhausted_items(
            Path(work_path), today, exhausted_items, min_prefix=reset_prefix
        )
        file_payload["exhausted"] = exhausted_items
        file_payload["schema_version"] = 2

    if updated_from_logs or healed_qps or (isinstance(file_payload, dict) and schema_v < 2):
        with _QUOTA_FILE_LOCK:
            try:
                base_data = file_payload if isinstance(file_payload, dict) else {"date": today, "exhausted": exhausted_items}
                base_data["date"] = today
                base_data["schema_version"] = 2
                base_data["exhausted"] = exhausted_items
                base_data["rpd_counts"] = dict(rpd_counts)
                quota_file.parent.mkdir(parents=True, exist_ok=True)
                tmp = quota_file.with_suffix(".tmp")
                tmp.write_text(json.dumps(base_data, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(tmp, quota_file)
            except Exception:
                pass

    key_set = set(api_keys) if api_keys else set()
    result: list[dict] = []
    for m in models_chain:
        rpd_per_key = get_model_rpd_per_key(m, cfg)
        limit = rpd_per_key * key_count
        used = rpd_counts.get(m, 0)
        exhausted_keys = sum(
            1
            for item in exhausted_items
            if item.get("model") == m and (not key_set or item.get("key") in key_set)
        )
        result.append({
            "model": m,
            "used": used,
            "limit": limit,
            "rpd_per_key": rpd_per_key,
            "key_count": key_count,
            "exhausted_keys": exhausted_keys,
            "display": f"{m}: {used}/{limit}",
        })
    return result


class LLMError(Exception):
    """Lỗi gọi model (đã hết số lần thử hoặc lỗi không thể thử lại)."""


class LLMCancelledError(Exception):
    """Tiến trình gọi AI bị hủy theo yêu cầu dừng của người dùng."""


_CANCEL_TLS = threading.local()


def get_active_cancel_event() -> threading.Event | None:
    """Trả về Event hủy đang gắn với luồng hiện tại (nếu có)."""
    return getattr(_CANCEL_TLS, "event", None)


def check_not_cancelled(cancel_event: threading.Event | None = None) -> None:
    """Ném LLMCancelledError ngay nếu người dùng đã yêu cầu dừng."""
    ev = cancel_event if cancel_event is not None else get_active_cancel_event()
    if ev is not None and ev.is_set():
        raise LLMCancelledError("Đã dừng chạy AI theo yêu cầu của người dùng.")


@contextlib.contextmanager
def bind_cancel_event(cancel_event: threading.Event | None):
    """Gắn cancel_event vào ngữ cảnh luồng hiện tại trong khối `with`."""
    prev = getattr(_CANCEL_TLS, "event", None)
    _CANCEL_TLS.event = cancel_event
    try:
        yield
    finally:
        _CANCEL_TLS.event = prev


def sleep_with_cancel(seconds: float, cancel_event: threading.Event | None = None) -> None:
    """Ngủ có thể ngắt ngay lập tức khi cancel_event được kích hoạt."""
    ev = cancel_event if cancel_event is not None else get_active_cancel_event()
    if seconds <= 0:
        check_not_cancelled(ev)
        return
    if ev is None:
        time.sleep(seconds)
        return
    if ev.wait(timeout=seconds):
        raise LLMCancelledError("Đã dừng chạy AI theo yêu cầu của người dùng.")


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


def _is_timeout_error(
    exc: BaseException | None,
    err_lower: str = "",
    status_code: int | None = None,
) -> bool:
    """Nhận diện mọi biến thể lỗi quá thời gian chờ (APITimeoutError, httpx TimeoutException, 408/504, ContinuationTimeoutError)."""
    if status_code in (408, 504):
        return True
    cur: BaseException | None = exc
    seen: set[int] = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, TimeoutError):
            return True
        name = type(cur).__name__.lower()
        if "timeout" in name:
            return True
        cur = getattr(cur, "__cause__", None) or getattr(cur, "__context__", None)
    low = (err_lower or (str(exc) if exc is not None else "")).lower()
    return any(tok in low for tok in (
        "timed out",
        "request timed out",
        "readtimeout",
        "connecttimeout",
        "writetimeout",
        "pooltimeout",
        "apitimeouterror",
        "continuationtimeout",
        "deadline_exceeded",
        "deadline exceeded",
        "gateway timeout",
    ))


def _is_connection_error(exc: BaseException | None, err_lower: str = "") -> bool:
    """Nhận diện lỗi rớt/kẹt kết nối HTTP (không phải timeout) để làm mới client pool."""
    cur: BaseException | None = exc
    seen: set[int] = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, ConnectionError):
            return True
        name = type(cur).__name__.lower()
        if any(k in name for k in ("connection", "connecterror", "readerror", "remoteprotocol", "noresponse")):
            return True
        cur = getattr(cur, "__cause__", None) or getattr(cur, "__context__", None)
    low = (err_lower or (str(exc) if exc is not None else "")).lower()
    return any(tok in low for tok in ("connection error", "remoteprotocol", "server disconnected", "broken pipe"))


def is_transient(exc: BaseException) -> bool:
    st = _status_of(exc)
    if st is not None:
        return st in TRANSIENT_STATUS
    if _is_timeout_error(exc) or _is_connection_error(exc):
        return True
    cur: BaseException | None = exc
    seen: set[int] = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        c_st = _status_of(cur)
        if c_st is not None:
            return c_st in TRANSIENT_STATUS
        name = type(cur).__name__.lower()
        if any(k in name for k in ("timeout", "connection", "connect", "network", "remoteprotocol",
                                   "readerror", "noresponse", "ratelimit", "unavailable")):
            return True
        if isinstance(cur, (TimeoutError, ConnectionError)):
            return True
        cur = getattr(cur, "__cause__", None) or getattr(cur, "__context__", None)
    return False


def _redact(text: str, secret: str | list[str] | None) -> str:
    if not text or not secret:
        return text
    secrets = [secret] if isinstance(secret, str) else secret
    for s in secrets:
        if s and isinstance(s, str):
            text = text.replace(s, "***")
    return text


def write_log(
    log_dir: Path | None,
    record: dict,
    secret: str | list[str] | None = None,
    *,
    log_path: Path | None = None,
) -> Path | None:
    if not log_dir and not log_path:
        return None
    import os

    if log_path is None:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        net_try = record.get("net_try", 0)
        suffix = f"_t{net_try}" if net_try else ""
        name = f"{ts}_{record.get('step', 'call')}_{record.get('attempt', 0)}{suffix}.json"
        log_path = log_dir / name
    else:
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)

    body = _redact(json.dumps(record, ensure_ascii=False, indent=1, default=str), secret)
    tmp = log_path.with_name(f".{log_path.name}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, log_path)
    except Exception:
        try:
            log_path.write_text(body, encoding="utf-8")
        except Exception:
            pass
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
    return log_path


def _parse_json(text: str) -> dict:
    import re
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    return json.loads(t)


class BaseClient:
    model = "base"
    _semaphore: threading.Semaphore | None = None
    _sem_limit: int = 0

    def __init__(self, cfg: dict):
        self.cfg = cfg
        from ..config import get_pool_scaling
        scaling = get_pool_scaling(cfg)
        limit = max(1, int(scaling["max_concurrent_calls"]))
        if BaseClient._semaphore is None or BaseClient._sem_limit != limit:
            BaseClient._semaphore = threading.Semaphore(limit)
            BaseClient._sem_limit = limit

    @classmethod
    def ensure_concurrency_limit(cls, min_limit: int) -> None:
        """Đảm bảo semaphore toàn cục cho phép ít nhất `min_limit` luồng gọi API song song."""
        if min_limit > cls._sem_limit:
            cls._semaphore = threading.Semaphore(min_limit)
            cls._sem_limit = min_limit

    def generate_json(self, step: str, system: str, prompt: str, schema: dict, *,
                      log_dir: Path | None = None, attempt: int = 0,
                      context: dict | None = None) -> LLMResult:
        raise NotImplementedError

    async def generate_json_async(
        self,
        step: str,
        system: str,
        prompt: str,
        schema: dict,
        *,
        log_dir: Path | None = None,
        attempt: int = 0,
        context: dict | None = None,
    ) -> LLMResult:
        """Gọi model bất đồng bộ (asyncio) không chặn event loop."""
        import asyncio

        return await asyncio.to_thread(
            self.generate_json,
            step=step,
            system=system,
            prompt=prompt,
            schema=schema,
            log_dir=log_dir,
            attempt=attempt,
            context=context,
        )

    async def generate_json_batch_async(self, calls: list[dict], max_concurrency: int = 4) -> list[LLMResult]:
        """Gọi model bất đồng bộ hàng loạt bằng asyncio.gather + asyncio.Semaphore."""
        import asyncio

        if not calls:
            return []
        sem = asyncio.Semaphore(max(1, max_concurrency))

        async def _run_one(c: dict) -> LLMResult:
            async with sem:
                return await self.generate_json_async(
                    step=c["step"],
                    system=c["system"],
                    prompt=c["prompt"],
                    schema=c["schema"],
                    log_dir=c.get("log_dir"),
                    attempt=c.get("attempt", 0),
                    context=c.get("context"),
                )

        return list(await asyncio.gather(*(_run_one(c) for c in calls)))

    def generate_json_batch(self, calls: list[dict], max_workers: int = 4) -> list[LLMResult]:
        """Gọi model đồng thời cho nhiều yêu cầu bằng ThreadPoolExecutor."""
        from concurrent.futures import ThreadPoolExecutor
        if not calls:
            return []
        workers = min(max_workers, max(1, len(calls)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(
                    self.generate_json,
                    step=c["step"],
                    system=c["system"],
                    prompt=c["prompt"],
                    schema=c["schema"],
                    log_dir=c.get("log_dir"),
                    attempt=c.get("attempt", 0),
                    context=c.get("context"),
                )
                for c in calls
            ]
            return [f.result() for f in futures]
