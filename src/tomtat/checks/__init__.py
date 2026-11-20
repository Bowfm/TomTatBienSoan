"""Package checks — kiểm tra cơ học bản trích chọn và bản biên soạn.

Tái cấu trúc thành các submodule chuyên biệt:
- core: Issue, CheckResult, constants mức độ lỗi (ERROR, WARNING, INFO)
- abstract_checks: kiểm tra bản biên soạn, độ dài, câu dẫn dắt đa văn bản
- extract_checks: kiểm tra bản trích chọn, tỷ lệ nén, tính nguyên văn, thứ tự
- scoring: ước lượng ROUGE-2, BARTScore, G-Eval
- feedback: run_checks, retry_feedback, needs_retry, quality_key
"""
from __future__ import annotations

from .abstract_checks import (
    BULLET_RE,
    FORBIDDEN_TRANSITIONS_RE,
    NO_BULLET_STYLES,
    NUM_RE,
    check_abstract,
    check_length,
    clean_abstract_text,
    clean_multi_doc_abstract,
)
from .core import (
    ERROR,
    INFO,
    LEVEL_VI,
    WARNING,
    CheckResult,
    Issue,
    _tokens_lower,
    is_heading_like,
)
from .extract_checks import check_extract
from .feedback import (
    needs_retry,
    quality_key,
    retry_feedback,
    run_checks,
)
from .scoring import (
    estimate_bartscore,
    estimate_geval_heuristic,
    estimate_rouge2,
)
from .suspicious import (
    BANK_KEYWORDS,
    VI_STOPWORDS,
    check_suspicious_document,
)

__all__ = [
    # Core & constants
    "ERROR",
    "WARNING",
    "INFO",
    "LEVEL_VI",
    "Issue",
    "CheckResult",
    "is_heading_like",
    "_tokens_lower",
    # Abstract
    "BULLET_RE",
    "NUM_RE",
    "NO_BULLET_STYLES",
    "FORBIDDEN_TRANSITIONS_RE",
    "check_abstract",
    "check_length",
    "clean_abstract_text",
    "clean_multi_doc_abstract",
    # Extract
    "check_extract",
    # Scoring
    "estimate_rouge2",
    "estimate_bartscore",
    "estimate_geval_heuristic",
    # Feedback & main run
    "run_checks",
    "retry_feedback",
    "needs_retry",
    "quality_key",
    # Suspicious re-exports
    "check_suspicious_document",
    "BANK_KEYWORDS",
    "VI_STOPWORDS",
]
