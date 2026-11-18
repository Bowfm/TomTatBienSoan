"""Xử lý văn bản tiếng Việt: chuẩn hóa NFC, đếm từ, tách câu.

Tái cấu trúc từ skill_ref/.../scripts/common.py, có sửa:
- viết tắt ghép (PGS.TS., Th.S.) không bị tách;
- đề mục "Điều 5. ...", "Chương II. ...", "I. ...", "a. ..." không bị tách sau số thứ tự;
- danh sách viết tắt và từ dẫn đề mục lấy từ cấu hình.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable
import unicodedata

DEFAULT_ABBREVIATIONS = frozenset({
    "tp", "ts", "pgs", "gs", "ths", "th", "bs", "ks", "cn", "tt", "tx", "q", "p", "đ",
    "nxb", "vd", "tr", "st", "ncs", "mr", "mrs", "ms", "dr", "no", "đc", "ubnd", "hđnd",
    "sđd", "tlđd",
})
DEFAULT_HEADING_WORDS = frozenset({
    "điều", "chương", "mục", "phần", "khoản", "bài", "tiết", "phụ lục",
})


@dataclass(frozen=True)
class TextConfig:
    """Cấu hình bất biến (immutable) cho tách câu và xử lý văn bản."""
    abbreviations: frozenset[str] = DEFAULT_ABBREVIATIONS
    heading_words: frozenset[str] = DEFAULT_HEADING_WORDS

    def split_sentences(self, text: str | None) -> list[str]:
        return split_sentences(text, config=self)

    def count_words(self, text: str | None, mode: str = "whitespace_alnum") -> int:
        return count_words(text, mode=mode)


TextProcessor = TextConfig


_active_config: TextConfig = TextConfig()

_SPLIT_RE = re.compile(r'([.!?…]+["”’\')\]]*)\s+')
_ROMAN_RE = re.compile(r"^[IVXLCDM]{1,6}$")
_QUOTES = '"“”‘’()[]'


def configure(abbreviations: Iterable[str] | None = None,
              heading_words: Iterable[str] | None = None) -> TextConfig:
    """Đặt danh sách viết tắt / từ dẫn đề mục (lấy từ config.yaml)."""
    global _active_config
    new_abbrevs = (
        frozenset(nfc(a).lower().strip(".") for a in abbreviations)
        if abbreviations is not None
        else _active_config.abbreviations
    )
    new_headings = (
        frozenset(nfc(h).lower() for h in heading_words)
        if heading_words is not None
        else _active_config.heading_words
    )
    _active_config = TextConfig(abbreviations=new_abbrevs, heading_words=new_headings)
    return _active_config


def get_active_config() -> TextConfig:
    """Lấy cấu hình xử lý văn bản hiện hành."""
    return _active_config


def nfc(text: str | None) -> str:
    return unicodedata.normalize("NFC", text or "")


def norm(text: str | None) -> str:
    """Chuẩn hóa Unicode NFC và gộp khoảng trắng."""
    return re.sub(r"\s+", " ", nfc(text)).strip()


def count_words(text: str | None, mode: str = "whitespace_alnum") -> int:
    """Đếm các token tách bằng khoảng trắng có chứa ít nhất một chữ/số.

    Emoji, '=))', gạch ngang đứng riêng không được tính.
    """
    if mode != "whitespace_alnum":
        raise ValueError(f"Chế độ đếm từ không hỗ trợ: {mode}")
    return sum(1 for t in nfc(text).split() if re.search(r"\w", t))


def lines_of(text: str | None) -> list[str]:
    return [ln for ln in nfc(text).split("\n") if ln.strip()]


def bigrams(text: str | None) -> list[tuple[str, str]]:
    toks = re.findall(r"\w+", nfc(text).lower())
    return list(zip(toks, toks[1:]))


def _is_number_marker(raw: str) -> bool:
    """'5', '12', 'II', 'a', '5a' - dùng cho số thứ tự / đề mục."""
    if re.fullmatch(r"\d{1,3}[a-zđ]?", raw, flags=re.I):
        return True
    if _ROMAN_RE.match(raw):
        return True
    return len(raw) == 1 and raw.isalpha()


def _no_split_after_dot(text: str, start: int, dot_pos: int, config: TextConfig | None = None) -> bool:
    """Quyết định dấu chấm (đơn) tại dot_pos có KHÔNG kết thúc câu hay không."""
    cfg = config or _active_config
    before = text[start:dot_pos].split()
    if not before:
        return False
    prior_raw = before[-1].strip(_QUOTES)
    prior = prior_raw.lower()
    # viết tắt, kể cả ghép: "TP", "PGS.TS", "Th.S"
    parts = [x for x in prior.split(".") if x]
    if parts and all(p in cfg.abbreviations for p in parts):
        return True
    # chữ cái viết tắt đứng riêng, ví dụ "Nguyễn Văn A."
    if len(prior) == 1 and prior.isalpha() and text[dot_pos - 1].isupper():
        return True
    # số thứ tự đầu câu: "1. ...", "II. ...", "a. ..."
    if len(before) == 1 and _is_number_marker(prior_raw):
        return True
    # đề mục: "Điều 5. ...", "Chương II. ...", "Phụ lục 1. ..."
    if 2 <= len(before) <= 3 and _is_number_marker(prior_raw):
        head = " ".join(before[:-1]).lower()
        if head in cfg.heading_words:
            return True
    return False


def split_sentences(text: str | None, config: TextConfig | None = None) -> list[str]:
    """Tách một đoạn thành các câu (gần đúng, thiên về KHÔNG tách khi mơ hồ)."""
    text = norm(text)
    if not text:
        return []
    cfg = config or _active_config
    out: list[str] = []
    start = 0
    for m in _SPLIT_RE.finditer(text):
        nxt = text[m.end():m.end() + 1]
        if not nxt or not (nxt.isupper() or nxt.isdigit() or nxt in '"“‘(['):
            continue
        punct = m.group(1).rstrip('"”’\')]')
        if punct == "." and _no_split_after_dot(text, start, m.start(), cfg):
            continue
        out.append(text[start:m.end()].strip())
        start = m.end()
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return out
