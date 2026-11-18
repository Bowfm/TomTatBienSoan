"""Core data structures and basic helpers for quality checks."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re

from ..textutil import count_words, nfc

ERROR, WARNING, INFO = "error", "warning", "info"
LEVEL_VI = {ERROR: "LỖI", WARNING: "CẢNH BÁO", INFO: "GHI CHÚ"}


@dataclass
class Issue:
    level: str
    code: str
    message: str
    target: str = "abstract"  # extract | abstract | label

    def to_dict(self) -> dict:
        d = asdict(self)
        d["level_vi"] = LEVEL_VI[self.level]
        return d


@dataclass
class CheckResult:
    metrics: dict = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    extract_sentences: list[dict] = field(default_factory=list)
    matched_units: list[dict] = field(default_factory=list)

    def add(self, level: str, code: str, message: str, target: str = "abstract") -> None:
        self.issues.append(Issue(level, code, message, target))

    def of(self, level: str | None = None, target: str | None = None) -> list[Issue]:
        return [
            i for i in self.issues
            if (level is None or i.level == level) and (target is None or i.target == target)
        ]

    @property
    def errors(self) -> list[Issue]:
        return self.of(ERROR)

    @property
    def warnings(self) -> list[Issue]:
        return self.of(WARNING)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "metrics": self.metrics,
            "issues": [i.to_dict() for i in self.issues],
            "n_errors": len(self.errors),
            "n_warnings": len(self.warnings),
            "extract_sentences": self.extract_sentences,
            "matched_units": self.matched_units,
        }

    def text_report(self) -> str:
        return "\n".join(f"[{LEVEL_VI[i.level]}] {i.message}" for i in self.issues)


def is_heading_like(line: str) -> bool:
    s = line.strip()
    return count_words(s) <= 14 and not re.search(r'[.!?…"”):;]$', s)


def _tokens_lower(text: str | None) -> list[str]:
    return re.findall(r"\w+", nfc(text).lower())
