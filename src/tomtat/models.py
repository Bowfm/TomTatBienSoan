"""Domain data structures, dataclasses, and TypedDicts for TomTatBienSoan.

Tập trung các mô hình dữ liệu dùng chung giữa các layer:
- LLM: LLMResult, LLMError
- Checks: CheckResult, Issue
- Text: TextConfig
- Pipeline & Workspace: FileState, StepStates, Units
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypedDict

from .checks.core import CheckResult, Issue
from .llm.base import LLMError, LLMResult
from .textutil import TextConfig


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
    label: LabelStep
    extract: ExtractStep
    abstract: AbstractStep
    judge: JudgeStep | None
    checks: dict[str, Any] | None
    current_version: int
    output_path: str | None
    model_used: str | None


@dataclass
class UnitItem:
    """Một đơn vị văn bản (câu hoặc đề mục) được phân đoạn từ .docx."""
    id: int
    text: str
    words: int
    is_heading: bool = False


@dataclass
class DocSegment:
    """Một văn bản con trong tài liệu (hỗ trợ đơn văn bản và đa văn bản)."""
    n: int
    words: int
    units: list[UnitItem] = field(default_factory=list)


@dataclass
class DocumentBudget:
    """Ngân sách từ và ngưỡng tỷ lệ nén trích chọn."""
    total_words: int
    extract_target_ratio: float
    extract_target_mid: int
    extract_min: int
    extract_max: int


__all__ = [
    # Checks
    "CheckResult",
    "Issue",
    # LLM
    "LLMResult",
    "LLMError",
    # Text
    "TextConfig",
    # State TypedDicts
    "LabelStep",
    "ExtractStep",
    "AbstractStep",
    "JudgeStep",
    "FileState",
    # Segmentation Dataclasses
    "UnitItem",
    "DocSegment",
    "DocumentBudget",
]
