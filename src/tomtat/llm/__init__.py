"""Package llm — LLM client abstraction layer.

Re-exports tất cả symbol public để các module cũ import từ `tomtat.llm` vẫn hoạt động.
"""
from .base import (
    BaseClient,
    LLMError,
    LLMResult,
    _get_quota_cycle_date,
    _parse_json,
    _redact,
    _status_of,
    is_transient,
    write_log,
)
from .gemini import GeminiClient
from .mock import MockClient

__all__ = [
    "BaseClient",
    "LLMError",
    "LLMResult",
    "GeminiClient",
    "MockClient",
    "write_log",
    "is_transient",
    "_get_quota_cycle_date",
    "_parse_json",
    "_redact",
    "_status_of",
]
