"""Fixtures and shared helpers for tomtat tests."""
from __future__ import annotations

from pathlib import Path
from docx import Document
import pytest


@pytest.fixture
def make_docx():
    """Fixture tạo file docx giả lập với bảng 1 cột chứa danh sách cell_texts."""
    def _make(path: Path, cell_texts: list[str]) -> Path:
        doc = Document()
        table = doc.add_table(rows=len(cell_texts), cols=1)
        for i, t in enumerate(cell_texts):
            table.rows[i].cells[0].text = t
        doc.save(str(path))
        return path
    return _make
