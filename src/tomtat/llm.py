"""Backward-compatibility shim — module llm đã được chuyển thành package src/tomtat/llm/."""
from .llm import (
    BaseClient,
    GeminiClient,
    LLMError,
    LLMResult,
    MockClient,
    _get_quota_cycle_date,
    _parse_json,
    _redact,
    _status_of,
    is_transient,
    write_log,
)

__all__ = [
    "BaseClient",
    "GeminiClient",
    "LLMError",
    "LLMResult",
    "MockClient",
    "is_transient",
    "write_log",
    "_get_quota_cycle_date",
    "_parse_json",
    "_redact",
    "_status_of",
]
