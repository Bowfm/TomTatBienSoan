"""Orchestration: run_checks, retry_feedback, needs_retry, quality_key."""
from __future__ import annotations

from .. import STYLES
from .suspicious import check_suspicious_document
from ..textutil import nfc
from .abstract_checks import check_abstract
from .core import ERROR, WARNING, CheckResult
from .extract_checks import check_extract


def run_checks(data: dict, style: str | None, extract: str | None, abstract: str | None,
               cfg: dict) -> CheckResult:
    """Kiểm tra toàn bộ. style/extract/abstract có thể rỗng (kiểm tra từng phần)."""
    res = CheckResult()
    style = nfc(style or "").strip()
    extract = nfc(extract or "")
    abstract = nfc(abstract or "")

    # Kiểm tra file bất thường có thể bị hủy
    suspicious_reasons = check_suspicious_document(data)
    if suspicious_reasons:
        res.metrics["is_suspicious"] = True
        res.metrics["suspicious_reasons"] = suspicious_reasons
        for reason in suspicious_reasons:
            res.add(WARNING, "suspicious_file", f"[CẢNH BÁO HỦY BÀI] {reason}", "extract")
    else:
        res.metrics["is_suspicious"] = False
        res.metrics["suspicious_reasons"] = []

    if not style:
        res.add(ERROR, "label_missing", "Chưa có nhãn phong cách chức năng.", "label")
    elif style not in STYLES:
        res.add(ERROR, "label_invalid", f"Nhãn '{style}' không thuộc 5 giá trị hợp lệ.", "label")
    check_extract(data, extract, cfg, res)
    check_abstract(data, style, extract, abstract, cfg, res)
    return res


def retry_feedback(res: CheckResult, cfg: dict, target: str = "abstract") -> list[str]:
    """Danh sách vấn đề cụ thể gửi lại cho model ở lần thử sau."""
    codes = set(cfg["abstract"].get("retry_on_warnings", []))
    return [i.message for i in res.issues
            if i.target == target and (i.level == ERROR or (i.level == WARNING and i.code in codes))]


def needs_retry(res: CheckResult, cfg: dict, target: str = "abstract") -> bool:
    return bool(retry_feedback(res, cfg, target))


def quality_key(res: CheckResult, n_target: float | None = None) -> tuple:
    """Khóa sắp xếp để chọn bản tốt nhất (nhỏ hơn = tốt hơn)."""
    n_abs = res.metrics.get("abstract_words", 0)
    dist = abs(n_abs - n_target) if n_target else 0
    return (len(res.errors), len(res.warnings), dist)
