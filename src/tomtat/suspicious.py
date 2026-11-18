"""Re-export từ tomtat.checks.suspicious để tương thích ngược với Phase 1."""
from __future__ import annotations

from .checks.suspicious import (
    BANK_KEYWORDS,
    VI_STOPWORDS,
    check_suspicious_document,
)

__all__ = ["BANK_KEYWORDS", "VI_STOPWORDS", "check_suspicious_document"]
