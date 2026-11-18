"""Kiểm thử tương tác file Word docx: layout, bảo vệ file gốc, tô vàng, dòng trống."""
from __future__ import annotations

from pathlib import Path
from docx.oxml.ns import qn
import pytest

from tomtat.config import load_config
from tomtat.docx_io import (
    DocxError,
    _set_cell_text,
    check_filename_convention,
    detect_layout,
    fill_docx,
    load_table,
    row_text,
)
from tomtat.textutil import count_words
from tomtat.units import assemble_extract, inspect_docx, parse_spec


def test_detect_layout_without_label_rows(tmp_path, make_docx):
    p = make_docx(tmp_path / "S001.docx", [
        "Câu hỏi truy vấn?",
        "Văn bản gốc duy nhất có nội dung đủ dài.",
        "Gợi ý trích chọn AI.",
        "Gợi ý biên soạn AI.",
    ])
    _, _, rows = load_table(p)
    layout = detect_layout(rows)
    assert "error" not in layout
    assert layout["label_rows_present"] is False
    assert layout["has_query"] is True
    assert layout["doc_idx"] == [1]
    assert layout["ai_extract_idx"] == 2
    assert layout["ai_abstract_idx"] == 3


def test_detect_layout_with_empty_label_rows_and_empty_query(tmp_path, make_docx):
    p = make_docx(tmp_path / "M002.docx", [
        "",
        "Văn bản 1.",
        "Văn bản 2.",
        "Gợi ý trích chọn AI.",
        "Gợi ý biên soạn AI.",
        "",
        "",
        "",
    ])
    _, _, rows = load_table(p)
    layout = detect_layout(rows)
    assert layout["label_rows_present"] is True
    assert layout["has_query"] is False
    assert layout["doc_idx"] == [1, 2]
    assert layout["label_idx"] == [5, 6, 7]


def test_filename_convention_check():
    assert check_filename_convention("M54629.docx", 2) is None
    assert check_filename_convention("S10001.docx", 1) is None
    assert check_filename_convention("M54629.docx", 1) is not None
    assert check_filename_convention("S10001.docx", 3) is not None


def test_assemble_extract_preserves_order_and_paragraphs(tmp_path, make_docx):
    cfg = load_config()
    doc_text = "Câu một đoạn một. Câu hai đoạn một.\nCâu ba đoạn hai. Câu bốn đoạn hai."
    p = make_docx(tmp_path / "S01.docx", ["", doc_text, "AI ext", "AI abs"])
    data = inspect_docx(p, cfg)
    spec = parse_spec("1: 4, 1-2")
    ext_text, per_doc = assemble_extract(data, spec)
    lines = ext_text.splitlines()
    assert len(lines) == 2
    assert lines[0] == "Câu một đoạn một. Câu hai đoạn một."
    assert lines[1] == "Câu bốn đoạn hai."
    assert per_doc[1] == count_words(ext_text)


def test_fill_docx_creates_yellow_rows_and_protects_input(tmp_path, make_docx):
    inp_dir = tmp_path / "input"
    out_dir = tmp_path / "output"
    inp_dir.mkdir()
    out_dir.mkdir()

    src = make_docx(inp_dir / "S03.docx", [
        "Query mẫu",
        "Văn bản gốc mẫu có một số câu.",
        "AI trích chọn",
        "AI biên soạn",
    ])
    with pytest.raises(DocxError):
        fill_docx(
            src, inp_dir / "other.docx",
            "khoa học", "Văn bản gốc mẫu.", "Bản biên soạn.",
            protected_dirs=[inp_dir],
        )

    dst = out_dir / "S03.docx"
    info = fill_docx(
        src, dst,
        "khoa học", "Văn bản gốc mẫu có một số câu.", "Bản biên soạn mẫu.",
        protected_dirs=[inp_dir],
    )
    assert info["rows_created"] is True
    assert dst.exists()

    _, table2, rows2 = load_table(dst)
    texts2 = [row_text(r) for r in rows2]
    assert len(texts2) == 7
    assert texts2[:4] == ["Query mẫu", "Văn bản gốc mẫu có một số câu.", "AI trích chọn", "AI biên soạn"]
    assert texts2[4] == "khoa học"
    assert texts2[5] == "Văn bản gốc mẫu có một số câu."
    assert texts2[6] == "Bản biên soạn mẫu."
    for idx in (4, 5, 6):
        shd = table2.rows[idx].cells[0]._element.tcPr.findall(qn("w:shd"))
        assert shd and shd[0].get(qn("w:fill")) == "FFFF00"


def test_docx_set_cell_text_preserves_blank_lines(tmp_path, make_docx):
    p = make_docx(tmp_path / "test_blank.docx", ["Nội dung cũ"])
    _, table, _ = load_table(p)
    cell = table.rows[0].cells[0]

    multi_abs = "Đoạn 1 của bài 1.\n\nĐoạn 2 của bài 2."
    _set_cell_text(cell, multi_abs)

    assert len(cell.paragraphs) == 3
    assert cell.paragraphs[0].text == "Đoạn 1 của bài 1."
    assert cell.paragraphs[1].text == ""
    assert cell.paragraphs[2].text == "Đoạn 2 của bài 2."
