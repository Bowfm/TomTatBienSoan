"""Package llm — LLM client abstraction layer.

Re-exports tất cả symbol public để các module cũ import từ `tomtat.llm` vẫn hoạt động.
"""
from .base import (
    BaseClient,
    DEFAULT_MIN_INTERVAL_S,
    DEFAULT_RPD_LIMITS,
    DEFAULT_RPM_LIMITS,
    DEFAULT_TIMEOUT_S,
    LLMCancelledError,
    LLMError,
    LLMResult,
    _get_quota_cycle_date,
    _parse_json,
    _redact,
    _status_of,
    bind_cancel_event,
    check_not_cancelled,
    get_active_cancel_event,
    get_model_min_interval_s,
    get_model_rpd_per_key,
    get_model_rpm_per_key,
    get_model_timeout_s,
    get_rpd_status,
    is_transient,
    sleep_with_cancel,
    write_log,
)
from .gemini import GeminiClient
from .mock import MockClient

__all__ = [
    "BaseClient",
    "DEFAULT_MIN_INTERVAL_S",
    "DEFAULT_RPD_LIMITS",
    "DEFAULT_RPM_LIMITS",
    "DEFAULT_TIMEOUT_S",
    "LLMCancelledError",
    "LLMError",
    "LLMResult",
    "GeminiClient",
    "MockClient",
    "bind_cancel_event",
    "check_not_cancelled",
    "get_active_cancel_event",
    "get_model_min_interval_s",
    "get_model_rpd_per_key",
    "get_model_rpm_per_key",
    "get_model_timeout_s",
    "get_rpd_status",
    "sleep_with_cancel",
    "write_log",
    "is_transient",
    "_get_quota_cycle_date",
    "_parse_json",
    "_redact",
    "_status_of",
]
