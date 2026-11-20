"""Lớp cơ sở, kiểu dữ liệu và tiện ích dùng chung cho tất cả LLM client."""
from __future__ import annotations

import contextlib
import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _pacific_dst_bounds_utc(year: int) -> tuple[datetime, datetime]:
    """Tính mốc chuyển đổi giờ mùa hè US/Pacific (PST <-> PDT) theo chuẩn UTC."""
    import calendar

    w_mar = calendar.weekday(year, 3, 1)
    dst_start = datetime(year, 3, 1 + (6 - w_mar) % 7 + 7, 10, 0, tzinfo=timezone.utc)
    w_nov = calendar.weekday(year, 11, 1)
    dst_end = datetime(year, 11, 1 + (6 - w_nov) % 7, 9, 0, tzinfo=timezone.utc)
    return dst_start, dst_end


def _get_quota_cycle_date() -> str:
    """Trả về ngày theo chu kỳ reset hạn mức của Google Gemini (00:00 Pacific Time, tức 15h VN mùa đông / 14h VN mùa hè)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
    except Exception:
        pass
    # Tự tính chuẩn theo múi giờ US/Pacific (PST UTC-8 / PDT UTC-7) không phụ thuộc thư viện ngoài
    now_utc = datetime.now(timezone.utc)
    dst_start, dst_end = _pacific_dst_bounds_utc(now_utc.year)
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
    Timeout (APITimeoutError), hoặc lỗi parse JSON sau khi nhận phản hồi có thể đã đi qua
    cổng kiểm soát nên được tính vào tổng ước tính RPD. Chỉ các lỗi bị chặn trước khi xử lý
    (429 RateLimit, 401/403 Auth, 404 NotFound, 400 BadRequest, 402 Billing, hoặc lỗi kết nối mạng cục bộ)
    là không tính thêm RPD.
    """
    if not err_str and status_code is None:
        return True
    if status_code is not None:
        if status_code in (500, 502, 503, 504, 408):
            return True
        if status_code in (400, 401, 402, 403, 404, 429):
            return False
    low = (err_str or "").lower()
    if any(k in low for k in (
        "429", "too_many_requests", "exceeded a quota", "quota exceeded",
        "resource has been exhausted", "unauthenticated", "not valid",
        "permission_denied", "not_found", "401", "403", "404",
        "bad_request", "invalid_argument", "payment_required", "400", "402",
    )):
        return False
    if _is_connection_error(None, low):
        return False
    if any(k in low for k in (
        "503", "500", "502", "504", "high demand", "service_unavailable",
        "internalservererror", "overloaded", "timeout", "timed out", "json",
    )):
        return True
    return True


def _get_quota_cycle_start_utc(cycle_date: str) -> datetime:
    """Tính thời điểm 00:00 Pacific Time của `cycle_date` dưới dạng UTC aware datetime."""
    try:
        from zoneinfo import ZoneInfo

        pt_tz = ZoneInfo("America/Los_Angeles")
        dt_pt = datetime.strptime(cycle_date, "%Y-%m-%d").replace(tzinfo=pt_tz)
        return dt_pt.astimezone(timezone.utc)
    except Exception:
        dt_naive = datetime.strptime(cycle_date, "%Y-%m-%d")
        dst_start, dst_end = _pacific_dst_bounds_utc(dt_naive.year)
        approx_utc = dt_naive.replace(tzinfo=timezone.utc) + timedelta(hours=8)
        offset_hours = -7 if dst_start <= approx_utc < dst_end else -8
        pt_tz = timezone(timedelta(hours=offset_hours))
        return dt_naive.replace(tzinfo=pt_tz).astimezone(timezone.utc)


def _get_quota_cycle_start_local_prefix(cycle_date: str) -> str:
    """Tính tiền tố tên file log 'YYYYMMDD-HHMMSS' theo giờ máy cục bộ tại thời điểm bắt đầu chu kỳ quota (00:00 Pacific Time)."""
    try:
        dt_local = _get_quota_cycle_start_utc(cycle_date).astimezone()
        return dt_local.strftime("%Y%m%d-%H%M%S")
    except Exception:
        return f"{cycle_date.replace('-', '')}-150000"


def _get_next_quota_reset_local_str(cycle_date: str) -> str:
    """Trả về thời điểm reset chu kỳ tiếp theo theo giờ máy cục bộ (dạng 'YYYY-MM-DD HH:MM')."""
    try:
        next_day = (datetime.strptime(cycle_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        dt_local = _get_quota_cycle_start_utc(next_day).astimezone()
        return dt_local.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "15:00 giờ VN"


def _safe_key_id(key: str) -> str:
    """Mã định danh an toàn (hash ngắn) cho API key dùng trong API/UI, tuyệt đối không lộ key thật."""
    return hashlib.sha256((key or "").encode("utf-8")).hexdigest()[:12]


def _format_key_label(index: int, key: str) -> str:
    """Nhãn hiển thị an toàn của API key: Key #<idx> (...xxxx)."""
    tail = key[-4:] if key and len(key) > 6 else "***"
    return f"Key #{index} (...{tail})"


_KEY_LABEL_RE = re.compile(r"Key\s*#(\d+)\s*\(\.\.\.([^)]+)\)")


def _match_log_key_label(key_label: str, api_keys: list[str]) -> str | None:
    """Ánh xạ `key_label` từ log về đúng API key hiện hành trong `api_keys` (ưu tiên khớp 4 ký tự cuối)."""
    if not key_label or not api_keys:
        return None
    idx_hint: int | None = None
    tail = ""
    m = _KEY_LABEL_RE.search(key_label)
    if m:
        try:
            idx_hint = int(m.group(1))
        except ValueError:
            idx_hint = None
        tail = m.group(2).strip()
    elif "(..." in key_label and key_label.endswith(")"):
        tail = key_label.rsplit("(...", 1)[-1][:-1].strip()

    if tail and tail != "***":
        matches = [k for k in api_keys if len(k) > 6 and k[-4:] == tail]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            if idx_hint is not None and 1 <= idx_hint <= len(api_keys) and api_keys[idx_hint - 1] in matches:
                return api_keys[idx_hint - 1]
            return matches[0]
        return None

    if tail == "***" and idx_hint is not None and 1 <= idx_hint <= len(api_keys):
        cand = api_keys[idx_hint - 1]
        if len(cand) <= 6:
            return cand
    return None


def _get_configured_models_chain(cfg: dict) -> list[str]:
    """Lấy toàn bộ danh sách model đã cấu hình (chuỗi chính + fallback + model riêng theo bước)."""
    g = cfg.get("gemini") or {}
    primary = g.get("model") or "gemini-4-hydrogen"
    fallbacks = g.get("fallback_models") or []
    models_chain = [primary] + [m for m in fallbacks if m and m != primary]
    for m_step in (g.get("model_by_step") or {}).values():
        if isinstance(m_step, str) and m_step.strip() and m_step.strip() not in models_chain:
            models_chain.append(m_step.strip())
    for fb_list in (g.get("fallback_models_by_step") or {}).values():
        if isinstance(fb_list, (list, tuple)):
            for m_fb in fb_list:
                if isinstance(m_fb, str) and m_fb.strip() and m_fb.strip() not in models_chain:
                    models_chain.append(m_fb.strip())
    return models_chain


def _classify_log_rpd_entry(d: dict) -> dict | None:
    """Phân loại một bản ghi log gọi API để thống kê RPD thành công vs lỗi 503/Timeout/429."""
    model = d.get("model")
    if not model:
        return None
    phase = d.get("phase") or ""
    in_prog = bool(d.get("in_progress", False))
    act = str(d.get("action") or "")
    st = str(d.get("status") or "")
    err = str(d.get("error") or "")
    err_low = err.lower()
    key_label = str(d.get("key_label") or "")
    started_at = str(d.get("started_at") or "")
    try:
        api_hops = max(1, int(d.get("api_hops") or 1))
    except (TypeError, ValueError):
        api_hops = 1
    try:
        completed_hops = max(0, int(d.get("completed_hops") or 0))
    except (TypeError, ValueError):
        completed_hops = 0

    if in_prog:
        if phase == "calling_api":
            return {
                "in_progress": True,
                "model": str(model),
                "key_label": key_label,
                "bucket": "in_progress",
                "is_qps_429": False,
                "is_quota_429": False,
                "api_hops": api_hops,
                "completed_hops": completed_hops,
                "action": act,
                "error": "",
                "started_at": started_at,
            }
        return None

    is_qps_429 = False
    is_quota_429 = False
    if st == "skipped" or act in ("cancelled_while_waiting", "requeue_after_wait"):
        bucket = "no_rpd"
    elif act == "cancelled_in_flight" or st == "cancelled":
        bucket = "cancelled"
    elif not err:
        bucket = "ok"
    elif not _error_consumes_rpd(err):
        bucket = "no_rpd"
        if _is_payload_qps_429(err_low) or act == "backoff_payload_qps":
            is_qps_429 = True
        elif (
            "429" in err_low
            or "resource_exhausted" in err_low
            or "resource has been exhausted" in err_low
            or "exceeded a quota" in err_low
            or "quota exceeded" in err_low
            or "too_many_requests" in err_low
            or act in ("mark_exhausted_rpd", "cooldown_rpm", "cooldown_429_strike", "cooldown_project_quota")
        ):
            is_quota_429 = True
    elif any(tok in err_low for tok in ("json", "max_output_tokens", "phản hồi rỗng")):
        bucket = "ok"
    elif _is_timeout_error(None, err_low):
        bucket = "timeout"
    else:
        bucket = "503"

    return {
        "in_progress": False,
        "model": str(model),
        "key_label": key_label,
        "bucket": bucket,
        "is_qps_429": is_qps_429,
        "is_quota_429": is_quota_429,
        "api_hops": api_hops,
        "completed_hops": completed_hops,
        "action": act,
        "error": err[:280] if err else "",
        "started_at": started_at,
    }


_LOG_RPD_CACHE_LOCK = threading.Lock()
_LOG_RPD_CACHE: dict = {
    "cycle_date": "",
    "work_dir": "",
    "start_prefix": "",
    "done_files": {},  # key_path -> classified dict
}


def _reconcile_rpd_details_from_logs(
    work_path: Path, cycle_date: str, min_prefix: str | None = None
) -> dict:
    """Quét log chu kỳ hiện tại (có cache tăng dần) trả về thống kê theo model và theo (key_label, model)."""
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
            _LOG_RPD_CACHE["done_files"] = {}

        done_files: dict[str, dict] = _LOG_RPD_CACHE["done_files"]
        existing_keys: set[str] = set()
        in_progress_entries: list[dict] = []

        if work_path.exists():
            for lp in sorted(work_path.glob("*/logs/*.json"), key=lambda p: p.name):
                if lp.name < start_prefix:
                    continue
                key_path = f"{lp.parent.parent.name}/{lp.name}"
                existing_keys.add(key_path)
                try:
                    mtime_ns = lp.stat().st_mtime_ns
                except OSError:
                    continue
                cached = done_files.get(key_path)
                if cached is not None and cached[0] == mtime_ns:
                    continue
                try:
                    d = json.loads(lp.read_text(encoding="utf-8"))
                except Exception:
                    continue
                classified = _classify_log_rpd_entry(d)
                if classified is None:
                    done_files.pop(key_path, None)
                    continue
                if classified["in_progress"]:
                    done_files.pop(key_path, None)
                    in_progress_entries.append(classified)
                else:
                    done_files[key_path] = (mtime_ns, classified)

        stale_keys = set(done_files.keys()) - existing_keys
        for dead in stale_keys:
            done_files.pop(dead, None)

        all_entries = [entry for _, entry in done_files.values()] + in_progress_entries

    by_model_total: dict[str, int] = {}
    by_model_ok: dict[str, int] = {}
    by_model_transient: dict[str, int] = {}
    by_model_qps: dict[str, int] = {}
    by_label_model: dict[tuple[str, str], dict] = {}

    for ent in all_entries:
        m = ent["model"]
        kl = ent["key_label"]
        b = ent["bucket"]
        hops = max(1, int(ent.get("api_hops") or 1))
        done_hops = max(0, int(ent.get("completed_hops") or 0))
        if b == "ok":
            tot_inc = hops
            ok_inc = hops
            tr_inc = 0
        elif b in ("503", "timeout", "cancelled"):
            ok_inc = done_hops
            tr_inc = 1
            tot_inc = ok_inc + tr_inc
        elif b == "in_progress":
            tot_inc = hops
            ok_inc = 0
            tr_inc = 0
        else:
            ok_inc = done_hops
            tr_inc = 0
            tot_inc = done_hops

        if tot_inc > 0:
            by_model_total[m] = by_model_total.get(m, 0) + tot_inc
        if ok_inc > 0:
            by_model_ok[m] = by_model_ok.get(m, 0) + ok_inc
        if tr_inc > 0:
            by_model_transient[m] = by_model_transient.get(m, 0) + tr_inc
        if ent["is_qps_429"]:
            by_model_qps[m] = by_model_qps.get(m, 0) + 1

        if kl:
            pair = (kl, m)
            st_box = by_label_model.get(pair)
            if st_box is None:
                st_box = {
                    "total": 0,
                    "ok": 0,
                    "err_503": 0,
                    "err_timeout": 0,
                    "cancelled": 0,
                    "in_progress": False,
                    "qps_429": 0,
                    "quota_429": 0,
                    "last_call_at": "",
                    "last_error": "",
                    "last_action": "",
                    "last_429_error": "",
                }
                by_label_model[pair] = st_box
            if ok_inc > 0:
                st_box["ok"] += ok_inc
            if b == "503":
                st_box["err_503"] += 1
            elif b == "timeout":
                st_box["err_timeout"] += 1
            elif b == "cancelled":
                st_box["cancelled"] += 1
            elif b == "in_progress":
                st_box["in_progress"] = True
            if tot_inc > 0:
                st_box["total"] += tot_inc

            if ent["is_qps_429"]:
                st_box["qps_429"] += 1
            if ent["is_quota_429"]:
                st_box["quota_429"] += 1
                if ent["error"]:
                    st_box["last_429_error"] = ent["error"]
            if ent["started_at"] >= st_box["last_call_at"]:
                st_box["last_call_at"] = ent["started_at"]
                if ent["error"]:
                    st_box["last_error"] = ent["error"]
                elif b == "ok":
                    st_box["last_error"] = ""
                if ent["action"]:
                    st_box["last_action"] = ent["action"]

    return {
        "by_model_total": by_model_total,
        "by_model_ok": by_model_ok,
        "by_model_transient": by_model_transient,
        "by_model_qps": by_model_qps,
        "by_label_model": by_label_model,
    }


def _reconcile_recent_rpm_from_logs(
    work_path: Path,
    api_keys: list[str],
    cycle_date: str,
    window_s: float = _RPM_WINDOW_S + 5.0,
    min_prefix: str = "",
) -> dict[tuple[str, str], list[float]]:
    """Khôi phục mốc thời gian gọi API thật trong ~65s gần nhất từ log để chặn vượt RPM sau khi restart server."""
    result: dict[tuple[str, str], list[float]] = {}
    if not work_path.exists() or not api_keys:
        return result
    now = time.time()
    cutoff = now - max(1.0, float(window_s))
    start_prefix = max(_get_quota_cycle_start_local_prefix(cycle_date), min_prefix or "")
    for lp in work_path.glob("*/logs/*.json"):
        if lp.name < start_prefix:
            continue
        try:
            if lp.stat().st_mtime < cutoff - 30.0:
                continue
            d = json.loads(lp.read_text(encoding="utf-8"))
        except Exception:
            continue
        act = str(d.get("action") or "")
        st = str(d.get("status") or "")
        if st == "skipped" or act in ("cancelled_while_waiting", "requeue_after_wait", "fallback_model_outage"):
            continue
        model = str(d.get("model") or "")
        kl = str(d.get("key_label") or "")
        if not model or not kl:
            continue
        matched_k = _match_log_key_label(kl, api_keys)
        if not matched_k:
            continue
        hop_epochs = d.get("hop_epochs")
        valid_ts: list[float] = []
        if isinstance(hop_epochs, list):
            for ts_v in hop_epochs:
                try:
                    ts_f = float(ts_v)
                except (TypeError, ValueError):
                    continue
                if cutoff < ts_f <= now + 5.0:
                    valid_ts.append(ts_f)
        if not valid_ts:
            try:
                ep = float(d.get("started_epoch") or 0.0)
            except (TypeError, ValueError):
                ep = 0.0
            if cutoff < ep <= now + 5.0:
                valid_ts.append(ep)
        if valid_ts:
            result.setdefault((matched_k, model), []).extend(valid_ts)
    for pair in result:
        result[pair].sort()
    return result


def _reconcile_rpd_from_logs(
    work_path: Path, cycle_date: str, min_prefix: str | None = None
) -> dict[str, int]:
    """Đối soát số lượt gọi API thực tế từ các file log trong chu kỳ ngày hiện tại (bao gồm cả lỗi 503, Timeout, JSON)."""
    details = _reconcile_rpd_details_from_logs(work_path, cycle_date, min_prefix=min_prefix)
    return details["by_model_total"]


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


def _load_and_reconcile_quota_state(cfg: dict, api_keys: list[str] | None = None) -> dict:
    """Đọc quota_exhausted.json và đối soát với log chu kỳ hiện tại (dùng chung cho get_rpd_status & get_key_rpd_details)."""
    import os
    import tomtat.llm as _llm_pkg

    if api_keys is None:
        from ..config import get_api_keys
        api_keys = get_api_keys()

    models_chain = _get_configured_models_chain(cfg)
    work_path = cfg.work_dir if hasattr(cfg, "work_dir") else Path((cfg.get("paths") or {}).get("work", "work"))
    quota_file = Path(work_path) / "quota_exhausted.json"
    today = _llm_pkg._get_quota_cycle_date()

    rpd_counts: dict[str, int] = {}
    exhausted_items: list[dict] = []
    exhausted_meta: dict[tuple[str, str], dict] = {}
    strikes_429: dict[tuple[str, str], int] = {}
    key_cooldown: dict[str, float] = {}
    key_rpd_saved: dict[tuple[str, str], dict] = {}
    file_payload: dict | None = None
    reset_prefix: str = ""
    now = time.time()

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
                    for item in data.get("exhausted_meta") or []:
                        k = str(item.get("key") or "")
                        m = str(item.get("model") or "")
                        if k and m:
                            exhausted_meta[(k, m)] = {
                                "reason": str(item.get("reason") or ""),
                                "error": str(item.get("error") or ""),
                                "at": str(item.get("at") or ""),
                            }
                    for item in data.get("strikes_429") or []:
                        k = str(item.get("key") or "")
                        m = str(item.get("model") or "")
                        cnt = item.get("count")
                        if k and m and isinstance(cnt, int) and cnt > 0:
                            strikes_429[(k, m)] = cnt
                    for k, ts in (data.get("key_cooldown") or {}).items():
                        if k and isinstance(ts, (int, float)) and ts > now:
                            key_cooldown[str(k)] = float(ts)
                    for item in data.get("key_rpd_counts") or []:
                        k = str(item.get("key") or "")
                        m = str(item.get("model") or "")
                        if k and m:
                            key_rpd_saved[(k, m)] = {
                                "total": max(0, int(item.get("total") or 0)),
                                "ok": max(0, int(item.get("ok") or 0)),
                                "transient": max(0, int(item.get("transient") or 0)),
                                "qps": max(0, int(item.get("qps") or 0)),
                            }
            except Exception:
                pass

    log_details = _reconcile_rpd_details_from_logs(Path(work_path), today, min_prefix=reset_prefix)
    log_counts = log_details["by_model_total"]
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

    return {
        "api_keys": list(api_keys),
        "models_chain": models_chain,
        "today": today,
        "rpd_counts": rpd_counts,
        "exhausted_items": exhausted_items,
        "exhausted_meta": exhausted_meta,
        "strikes_429": strikes_429,
        "key_cooldown": key_cooldown,
        "key_rpd_saved": key_rpd_saved,
        "log_details": log_details,
    }


def get_rpd_status(cfg: dict, api_keys: list[str] | None = None) -> list[dict]:
    """Lấy thống kê RPD (Requests Per Day) của từng model trong chuỗi cấu hình.

    Mỗi phần tử có dạng:
    {
        "model": str,
        "used": int,
        "ok_used": int,
        "transient_used": int,
        "limit": int,
        "rpd_per_key": int,
        "key_count": int,
        "exhausted_keys": int,
        "available_keys": int,
        "display": "[Tên model]: [used]/[limit]",
    }
    """
    state = _load_and_reconcile_quota_state(cfg, api_keys=api_keys)
    keys = state["api_keys"]
    key_count = max(1, len(keys))
    key_set = set(keys) if keys else set()
    rpd_counts = state["rpd_counts"]
    exhausted_items = state["exhausted_items"]
    log_ok = state["log_details"]["by_model_ok"]
    log_tr = state["log_details"]["by_model_transient"]

    exhausted_by_model: dict[str, set[str]] = {}
    for item in exhausted_items:
        k = item.get("key")
        m = item.get("model")
        if k and m and (not key_set or k in key_set):
            exhausted_by_model.setdefault(m, set()).add(k)

    result: list[dict] = []
    for m in state["models_chain"]:
        rpd_per_key = get_model_rpd_per_key(m, cfg)
        limit = rpd_per_key * key_count
        used = rpd_counts.get(m, 0)
        exhausted_keys = len(exhausted_by_model.get(m, set()))
        result.append({
            "model": m,
            "used": used,
            "ok_used": log_ok.get(m, 0),
            "transient_used": log_tr.get(m, 0),
            "limit": limit,
            "rpd_per_key": rpd_per_key,
            "key_count": key_count,
            "exhausted_keys": exhausted_keys,
            "available_keys": max(0, key_count - exhausted_keys),
            "display": f"{m}: {used}/{limit}",
        })
    return result


_EXHAUSTED_REASON_VI = {
    "429_daily": "Google báo hết RPD ngày (429)",
    "429_probe": "Kiểm tra trực tiếp nhận 429 từ Google",
    "429_rpm_3x": "Chạm giới hạn RPM/TPM 3 lần liên tiếp",
    "429_project_3x": "Chạm quota project (429) 3 lần liên tiếp",
    "invalid_key": "Key không hợp lệ / bị từ chối quyền (401/403)",
    "manual": "Đánh dấu hết RPD",
    "legacy": "Đánh dấu từ phiên trước (có thể do bộ đếm cũ hoặc 429)",
}


def get_key_rpd_details(
    cfg: dict,
    api_keys: list[str] | None = None,
    live_client=None,
) -> dict:
    """Trả về ma trận chi tiết trạng thái còn/hết RPD của từng model theo từng API key để đối chiếu với Google AI Studio."""
    state = _load_and_reconcile_quota_state(cfg, api_keys=api_keys)
    keys = state["api_keys"]
    models_chain = state["models_chain"]
    today = state["today"]
    now = time.time()

    exhausted_pairs: set[tuple[str, str]] = {
        (str(item.get("key") or ""), str(item.get("model") or ""))
        for item in state["exhausted_items"]
        if item.get("key") and item.get("model")
    }
    exhausted_meta: dict[tuple[str, str], dict] = dict(state["exhausted_meta"])
    strikes_429: dict[tuple[str, str], int] = dict(state["strikes_429"])
    key_cooldown: dict[str, float] = dict(state["key_cooldown"])
    key_rpd_saved: dict[tuple[str, str], dict] = dict(state["key_rpd_saved"])

    # Hợp nhất trạng thái RAM trực tiếp từ GeminiClient (nếu server đang giữ client sống)
    if live_client is not None and hasattr(live_client, "_key_lock"):
        try:
            with live_client._key_lock:
                if hasattr(live_client, "_check_and_reset_cycle"):
                    live_client._check_and_reset_cycle()
                if getattr(live_client, "_current_cycle_date", today) == today:
                    exhausted_pairs = set(getattr(live_client, "_exhausted", exhausted_pairs))
                    for pair, meta in (getattr(live_client, "_exhausted_meta", None) or {}).items():
                        exhausted_meta[pair] = dict(meta)
                    strikes_429 = dict(getattr(live_client, "_idle_429_strikes", strikes_429))
                    key_cooldown = {
                        k: ts
                        for k, ts in (getattr(live_client, "_key_cooldown", None) or {}).items()
                        if ts > now
                    }
                    live_totals = getattr(live_client, "_key_rpd_counts", None) or {}
                    live_oks = getattr(live_client, "_key_ok_counts", None) or {}
                    live_trs = getattr(live_client, "_key_transient_counts", None) or {}
                    live_qps = getattr(live_client, "_key_qps_counts", None) or {}
                    for pair in set(live_totals) | set(live_oks) | set(live_trs) | set(live_qps):
                        prev = key_rpd_saved.get(pair) or {"total": 0, "ok": 0, "transient": 0, "qps": 0}
                        key_rpd_saved[pair] = {
                            "total": max(prev["total"], int(live_totals.get(pair, 0))),
                            "ok": max(prev["ok"], int(live_oks.get(pair, 0))),
                            "transient": max(prev["transient"], int(live_trs.get(pair, 0))),
                            "qps": max(prev.get("qps", 0), int(live_qps.get(pair, 0))),
                        }
        except Exception:
            pass

    # Ánh xạ log (key_label, model) -> (key, model)
    by_pair_log: dict[tuple[str, str], dict] = {}
    for (kl, m), lstat in state["log_details"]["by_label_model"].items():
        matched_k = _match_log_key_label(kl, keys)
        if not matched_k:
            continue
        pair = (matched_k, m)
        cur = by_pair_log.get(pair)
        if cur is None:
            by_pair_log[pair] = dict(lstat)
        else:
            cur["total"] += lstat["total"]
            cur["ok"] += lstat["ok"]
            cur["err_503"] += lstat["err_503"]
            cur["err_timeout"] += lstat["err_timeout"]
            cur["cancelled"] += lstat["cancelled"]
            cur["in_progress"] = cur["in_progress"] or lstat["in_progress"]
            cur["qps_429"] += lstat["qps_429"]
            cur["quota_429"] += lstat["quota_429"]
            if lstat["last_call_at"] >= cur["last_call_at"]:
                cur["last_call_at"] = lstat["last_call_at"]
                if lstat["last_error"]:
                    cur["last_error"] = lstat["last_error"]
                if lstat["last_action"]:
                    cur["last_action"] = lstat["last_action"]
            if lstat["last_429_error"]:
                cur["last_429_error"] = lstat["last_429_error"]

    model_agg: dict[str, dict] = {
        m: {
            "model": m,
            "rpd_per_key": get_model_rpd_per_key(m, cfg),
            "rpm_per_key": get_model_rpm_per_key(m, cfg),
            "limit": get_model_rpd_per_key(m, cfg) * max(1, len(keys)),
            "used": state["rpd_counts"].get(m, 0),
            "ok_used": 0,
            "transient_used": 0,
            "qps_429_used": 0,
            "total_sent_with_qps": state["rpd_counts"].get(m, 0),
            "available_keys": 0,
            "exhausted_keys": 0,
            "cooldown_keys": 0,
            "strike_keys": 0,
        }
        for m in models_chain
    }

    keys_rows: list[dict] = []
    for idx, k in enumerate(keys, start=1):
        cd_rem = max(0, int(round(key_cooldown.get(k, 0.0) - now)))
        k_models: dict[str, dict] = {}
        k_total_used = 0
        k_total_ok = 0
        k_total_tr = 0
        k_total_qps = 0
        k_exh_models = 0

        for m in models_chain:
            pair = (k, m)
            rpd_lim = model_agg[m]["rpd_per_key"]
            lstat = by_pair_log.get(pair) or {}
            saved = key_rpd_saved.get(pair) or {}

            ok_used = max(int(lstat.get("ok") or 0), int(saved.get("ok") or 0))
            err_503_used = int(lstat.get("err_503") or 0)
            err_to_used = int(lstat.get("err_timeout") or 0)
            cancelled_used = int(lstat.get("cancelled") or 0)
            tr_used = max(err_503_used + err_to_used + cancelled_used, int(saved.get("transient") or 0))
            total_used = max(int(lstat.get("total") or 0), int(saved.get("total") or 0), ok_used + tr_used)

            is_exh = pair in exhausted_pairs
            strikes = int(strikes_429.get(pair) or 0)
            meta = exhausted_meta.get(pair) or {}
            reason_code = str(meta.get("reason") or "")
            reason_err = str(meta.get("error") or "")

            if is_exh and not reason_code:
                last_act = str(lstat.get("last_action") or "")
                last_429 = str(lstat.get("last_429_error") or "")
                if last_act == "invalid_key":
                    reason_code = "invalid_key"
                    reason_err = reason_err or str(lstat.get("last_error") or "")
                elif last_429:
                    low429 = last_429.lower()
                    if any(t in low429 for t in ("per day", "per_day", "perday", "daily", "rpd", "limit: 0")):
                        reason_code = "429_daily"
                    elif any(t in low429 for t in ("per minute", "per_minute", "perminute", "rpm", "tpm")):
                        reason_code = "429_rpm_3x"
                    else:
                        reason_code = "429_project_3x"
                    reason_err = reason_err or last_429
                else:
                    reason_code = "legacy"

            if is_exh:
                k_exh_models += 1
                model_agg[m]["exhausted_keys"] += 1
                if reason_code == "invalid_key":
                    st_code = "invalid_key"
                    st_vi = "Key lỗi (401/403)"
                else:
                    st_code = "exhausted"
                    st_vi = "Hết RPD"
            elif cd_rem > 0:
                model_agg[m]["available_keys"] += 1
                model_agg[m]["cooldown_keys"] += 1
                st_code = "cooldown"
                st_vi = f"Cooldown ({cd_rem}s)"
            elif strikes > 0:
                model_agg[m]["available_keys"] += 1
                model_agg[m]["strike_keys"] += 1
                st_code = "warning_429"
                st_vi = f"Cảnh báo 429 ({strikes}/3)"
            else:
                model_agg[m]["available_keys"] += 1
                st_code = "available"
                st_vi = "Còn RPD"

            qps_count = max(int(lstat.get("qps_429") or 0), int(saved.get("qps") or 0))
            k_total_used += total_used
            k_total_ok += ok_used
            k_total_tr += tr_used
            k_total_qps += qps_count
            model_agg[m]["ok_used"] += ok_used
            model_agg[m]["transient_used"] += tr_used
            model_agg[m]["qps_429_used"] += qps_count

            k_models[m] = {
                "model": m,
                "status": st_code,
                "status_vi": st_vi,
                "exhausted": is_exh,
                "rpd_limit": rpd_lim,
                "used": total_used,
                "ok_used": ok_used,
                "transient_used": tr_used,
                "err_503_used": err_503_used,
                "err_timeout_used": err_to_used,
                "cancelled_used": cancelled_used,
                "remaining_est": max(0, rpd_lim - total_used),
                "remaining_ok_est": max(0, rpd_lim - ok_used),
                "strikes_429": strikes,
                "qps_429_count": qps_count,
                "total_sent_with_qps": total_used + qps_count,
                "quota_429_count": int(lstat.get("quota_429") or 0),
                "cooldown_remaining_s": cd_rem,
                "reason_code": reason_code,
                "reason_vi": _EXHAUSTED_REASON_VI.get(reason_code, "") if is_exh else "",
                "last_error": reason_err or str(lstat.get("last_error") or ""),
                "last_call_at": str(meta.get("at") or lstat.get("last_call_at") or ""),
                "in_progress": bool(lstat.get("in_progress", False)),
            }

        keys_rows.append({
            "index": idx,
            "key_id": _safe_key_id(k),
            "key_tail": k[-4:] if len(k) > 6 else "***",
            "key_label": _format_key_label(idx, k),
            "cooldown_remaining_s": cd_rem,
            "total_used": k_total_used,
            "total_ok_used": k_total_ok,
            "total_transient_used": k_total_tr,
            "total_qps_429": k_total_qps,
            "total_sent_with_qps": k_total_used + k_total_qps,
            "exhausted_models": k_exh_models,
            "models": k_models,
        })

    # Đồng bộ ok_used/transient_used/qps_429_used ở tầng model với log_details khi chưa khớp hoàn toàn
    log_ok_m = state["log_details"]["by_model_ok"]
    log_tr_m = state["log_details"]["by_model_transient"]
    log_qps_m = state["log_details"].get("by_model_qps") or {}
    for m in models_chain:
        agg = model_agg[m]
        agg["ok_used"] = max(agg["ok_used"], int(log_ok_m.get(m, 0)))
        agg["transient_used"] = max(agg["transient_used"], int(log_tr_m.get(m, 0)))
        agg["used"] = max(agg["used"], agg["ok_used"] + agg["transient_used"])
        agg["qps_429_used"] = max(agg["qps_429_used"], int(log_qps_m.get(m, 0)))
        agg["total_sent_with_qps"] = agg["used"] + agg["qps_429_used"]

    return {
        "cycle_date": today,
        "reset_time_vi": "15:00 giờ VN (00:00 Pacific Time)",
        "next_reset_local": _get_next_quota_reset_local_str(today),
        "key_count": len(keys),
        "models": [model_agg[m] for m in models_chain],
        "keys": keys_rows,
    }


def unlock_quota_on_disk(
    cfg: dict,
    api_keys: list[str] | None = None,
    *,
    key: str | None = None,
    model: str | None = None,
) -> int:
    """Mở khóa trạng thái hết RPD / strikes_429 / cooldown trong quota_exhausted.json trên đĩa."""
    import os
    import tomtat.llm as _llm_pkg

    if api_keys is None:
        from ..config import get_api_keys
        api_keys = get_api_keys()

    work_path = cfg.work_dir if hasattr(cfg, "work_dir") else Path((cfg.get("paths") or {}).get("work", "work"))
    quota_file = Path(work_path) / "quota_exhausted.json"
    today = _llm_pkg._get_quota_cycle_date()
    unlocked = 0

    with _QUOTA_FILE_LOCK:
        if not quota_file.exists():
            return 0
        try:
            data = json.loads(quota_file.read_text(encoding="utf-8"))
        except Exception:
            return 0
        if data.get("date") != today:
            return 0

        def _matches(k_val: str | None, m_val: str | None) -> bool:
            k_ok = (key is None) or (k_val == key)
            m_ok = (model is None) or (m_val == model)
            return k_ok and m_ok

        new_exh = []
        for item in data.get("exhausted") or []:
            if _matches(item.get("key"), item.get("model")):
                unlocked += 1
            else:
                new_exh.append(item)
        data["exhausted"] = new_exh

        if "exhausted_meta" in data:
            data["exhausted_meta"] = [
                item for item in (data.get("exhausted_meta") or [])
                if not _matches(item.get("key"), item.get("model"))
            ]

        new_strikes = []
        for item in data.get("strikes_429") or []:
            if _matches(item.get("key"), item.get("model")):
                unlocked += 1 if not new_exh else 0
            else:
                new_strikes.append(item)
        if new_strikes:
            data["strikes_429"] = new_strikes
        else:
            data.pop("strikes_429", None)

        if "key_cooldown" in data:
            if key is None and model is None:
                data.pop("key_cooldown", None)
            elif key is not None:
                cds = dict(data.get("key_cooldown") or {})
                if cds.pop(key, None) is not None:
                    unlocked = max(unlocked, 1)
                if cds:
                    data["key_cooldown"] = cds
                else:
                    data.pop("key_cooldown", None)

        if "next_allowed_ts" in data:
            nats = [
                item for item in (data.get("next_allowed_ts") or [])
                if not _matches(item.get("key"), item.get("model"))
            ]
            if nats:
                data["next_allowed_ts"] = nats
            else:
                data.pop("next_allowed_ts", None)

        tmp = quota_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, quota_file)

    return unlocked


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
        "deadline expired",
        "gateway timeout",
    ))


def _is_server_deadline_exceeded(
    exc: BaseException | None,
    err_lower: str = "",
    status_code: int | None = None,
) -> bool:
    """Nhận diện lỗi hết thời gian chờ phía máy chủ Google (HTTP 504 / deadline_exceeded / gateway timeout)."""
    if status_code == 504:
        return True
    low = (err_lower or (str(exc) if exc is not None else "")).lower()
    return any(tok in low for tok in (
        "error code: 504",
        "deadline_exceeded",
        "deadline exceeded",
        "deadline expired",
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
    _sem_lock: threading.Lock = threading.Lock()

    def __init__(self, cfg: dict):
        self.cfg = cfg
        from ..config import get_pool_scaling
        scaling = get_pool_scaling(cfg)
        limit = max(1, int(scaling["max_concurrent_calls"]))
        with BaseClient._sem_lock:
            if BaseClient._semaphore is None or BaseClient._sem_limit != limit:
                BaseClient._semaphore = threading.Semaphore(limit)
                BaseClient._sem_limit = limit

    @classmethod
    def ensure_concurrency_limit(cls, min_limit: int) -> None:
        """Đảm bảo semaphore toàn cục cho phép ít nhất `min_limit` luồng gọi API song song."""
        with cls._sem_lock:
            if cls._semaphore is None or min_limit > cls._sem_limit:
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
