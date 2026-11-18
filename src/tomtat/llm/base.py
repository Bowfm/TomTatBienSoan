"""Lớp cơ sở, kiểu dữ liệu và tiện ích dùng chung cho tất cả LLM client."""
from __future__ import annotations

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
_RPM_SOFT_LIMIT = 4          # ưu tiên key dưới ngưỡng này (Free Tier phổ biến: 5 RPM)
_COOLDOWN_S = 30             # thời gian cooldown sau khi key bị 429 (giây)


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


def write_log(log_dir: Path | None, record: dict, secret: str | list[str] | None = None) -> None:
    if not log_dir:
        return
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    name = f"{ts}_{record.get('step', 'call')}_{record.get('attempt', 0)}.json"
    body = json.dumps(record, ensure_ascii=False, indent=1, default=str)
    (log_dir / name).write_text(_redact(body, secret), encoding="utf-8")


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
        g = cfg.get("gemini", {})
        limit = max(1, int(g.get("max_concurrent_calls", 4)))
        if BaseClient._semaphore is None or BaseClient._sem_limit != limit:
            BaseClient._semaphore = threading.Semaphore(limit)
            BaseClient._sem_limit = limit

    def generate_json(self, step: str, system: str, prompt: str, schema: dict, *,
                      log_dir: Path | None = None, attempt: int = 0,
                      context: dict | None = None) -> LLMResult:
        raise NotImplementedError

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
