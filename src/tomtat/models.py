"""Mô hình dữ liệu dùng chung (type hints) cho TomTatBienSoan.

Dữ liệu thực tế được lưu dưới dạng JSON (state.json, units.json) và truyền đi
dưới dạng dict, nên các mô hình ở đây là ``TypedDict``: chỉ phục vụ kiểm tra kiểu
tĩnh / gợi ý IDE, KHÔNG thay đổi cách đọc ghi dữ liệu lúc chạy.

Cấu trúc phải khớp với:
- ``state.FileWorkspace.default_state()``  → FileState
- ``units.inspect_docx()``                → UnitsData
- ``units.compute_budget()``              → DocumentBudget

Ngoài ra re-export các kiểu đã có ở module khác để import tập trung:
CheckResult, Issue, LLMResult, LLMError, TextConfig.
"""
from __future__ import annotations

from typing import Any, TypedDict


# ---------------------------------------------------------------- state.json
class LabelStep(TypedDict, total=False):
    value: str
    per_doc: list[dict[str, Any]]
    confidence: str
    reason: str
    computed_majority: str
    model_used: str | None


class ExtractStep(TypedDict, total=False):
    selections: dict[str, list[int]]
    text: str
    words: int
    compress: float
    note: str
    attempts: int
    custom_edited: bool
    model_used: str | None


class AbstractStep(TypedDict, total=False):
    text: str
    words: int
    note: str
    attempts: int
    model_used: str | None


class JudgeStep(TypedDict, total=False):
    relevance: float
    coherence: float
    consistency: float
    fluency: float
    issues: list[str]
    summary: str
    model_used: str | None


class FileState(TypedDict, total=False):
    file: str
    stem: str
    source_path: str
    status: str
    status_vi: str
    updated_at: str
    flags: list[str]
    error_message: str | None
    stale_steps: list[str]
    stale_reason: str | None
    label: LabelStep
    extract: ExtractStep
    abstract: AbstractStep
    judge: JudgeStep | None
    checks: dict[str, Any] | None
    current_version: int
    output_path: str | None
    model_used: str | None
    current_step: str | None


# ---------------------------------------------------------------- units.json
class UnitItem(TypedDict):
    """Một đơn vị (câu/đề mục) — xem ``units.build_units``."""
    id: int
    para: int
    text: str
    words: int


class DocSegment(TypedDict):
    """Một văn bản con trong file (file M có nhiều văn bản)."""
    n: int
    row: int
    words: int
    units: list[UnitItem]


class DocumentBudget(TypedDict):
    """Ngân sách số từ — xem ``units.compute_budget``."""
    total_words: int
    extract_min: int
    extract_max: int
    extract_target_low: int
    extract_target_high: int
    extract_target_mid: int
    target_low_pct: int
    target_high_pct: int
    target_mid_pct: int


class UnitsData(TypedDict):
    """Kết quả bước inspect — xem ``units.inspect_docx``."""
    file: str
    kind: str
    query: str
    layout: dict[str, Any]
    docs: list[DocSegment]
    ai_extract: str
    ai_abstract: str
    existing_labels: list[str]
    budget: DocumentBudget
    filename_mismatch: str | None


# ---------------------------------------------------------------- Re-exports (cuối file để tránh circular import)
from .checks.core import CheckResult, Issue  # noqa: E402
from .llm.base import LLMError, LLMResult  # noqa: E402
from .textutil import TextConfig  # noqa: E402

__all__ = [
    # Re-export
    "CheckResult",
    "Issue",
    "LLMResult",
    "LLMError",
    "TextConfig",
    # state.json
    "LabelStep",
    "ExtractStep",
    "AbstractStep",
    "JudgeStep",
    "FileState",
    # units.json
    "UnitItem",
    "DocSegment",
    "DocumentBudget",
    "UnitsData",
]
