"""Đọc / ghi bảng trong file .docx của dự án.

Tái cấu trúc từ skill_ref/.../scripts/common.py (load_table, detect_layout) và fill_docx.py.
"""
from __future__ import annotations

import copy
import os
import re
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from . import STYLES
from .textutil import count_words, nfc


class DocxError(Exception):
    """Lỗi cấu trúc file .docx (thông điệp tiếng Việt)."""


# ------------------------------------------------------------------ đọc

def paragraphs_of(cell) -> list[str]:
    """Danh sách đoạn (chuỗi, NFC) của một ô, tách cả xuống dòng mềm."""
    paras: list[str] = []
    for p in cell.paragraphs:
        for line in p.text.split("\n"):
            paras.append(nfc(line))
    return paras


def load_table(path: str | os.PathLike):
    doc = Document(str(path))
    if not doc.tables:
        raise DocxError("File không có bảng nào.")
    table = doc.tables[0]
    rows = [paragraphs_of(r.cells[0]) for r in table.rows]
    return doc, table, rows


def row_text(paras: list[str]) -> str:
    return "\n".join(p for p in paras if p.strip()).strip()


def is_blank_or_placeholder(t: str) -> bool:
    t = (t or "").strip()
    return (not t) or bool(re.fullmatch(r"<[^<>]{0,80}>", t))


def detect_layout(rows: list[list[str]]) -> dict:
    """Nhận diện vai trò các hàng. rows: list[list[str]] (đoạn trong từng ô).

    Trả về dict; có khóa "error" nếu không nhận diện được.
    """
    texts = [row_text(r) for r in rows]
    n = len(texts)
    notes: list[str] = []
    if n < 4:
        return {"error": f"Bảng chỉ có {n} hàng, ít hơn mức tối thiểu 4 "
                         "(query, 1 văn bản, 2 hàng gợi ý của AI)."}
    label_rows = 0
    if n >= 7:
        tail = texts[-3:]
        if texts[-3].strip().lower() in STYLES:
            label_rows = 3
        elif is_blank_or_placeholder(texts[-1]):
            label_rows = 3
        elif all(is_blank_or_placeholder(t) for t in tail):
            label_rows = 3
    elif is_blank_or_placeholder(texts[-1]):
        notes.append("Hàng cuối trống nhưng bảng quá ngắn để có đủ 3 hàng nhãn; "
                     "kiểm tra lại cấu trúc.")
    ai_extract_idx = n - label_rows - 2
    ai_abstract_idx = n - label_rows - 1
    doc_idx = list(range(1, ai_extract_idx))
    if not doc_idx:
        return {"error": "Không xác định được hàng văn bản gốc nào."}
    for i in doc_idx:
        if is_blank_or_placeholder(texts[i]):
            notes.append(f"Hàng {i + 1} được coi là văn bản gốc nhưng đang trống.")
    label_idx = [n - 3, n - 2, n - 1] if label_rows == 3 else []
    existing = [i for i in label_idx
                if not is_blank_or_placeholder(texts[i]) and texts[i].strip().lower() not in STYLES]
    if existing:
        notes.append("3 hàng nhãn đã có nội dung ở hàng "
                     f"{[i + 1 for i in existing]}; sẽ cần xác nhận trước khi ghi đè.")
    query = texts[0]
    return {
        "n_rows": n,
        "label_rows_present": label_rows == 3,
        "query_idx": 0,
        "has_query": not is_blank_or_placeholder(query),
        "doc_idx": doc_idx,
        "ai_extract_idx": ai_extract_idx,
        "ai_abstract_idx": ai_abstract_idx,
        "label_idx": label_idx,
        "label_rows_with_content": existing,
        "notes": notes,
    }


def filename_kind(name: str) -> str | None:
    """'M' (đa văn bản), 'S' (đơn văn bản) hoặc None theo quy ước đặt tên."""
    stem = Path(name).name.strip()
    if not stem:
        return None
    c = stem[0].upper()
    return c if c in ("M", "S") else None


def check_filename_convention(name: str, n_docs: int) -> str | None:
    """Trả về thông điệp nếu tên file lệch với số văn bản nhận diện được."""
    kind = filename_kind(name)
    if kind is None:
        return (f"Tên file '{Path(name).name}' không bắt đầu bằng M hoặc S; "
                "không đối chiếu được loại đơn/đa văn bản.")
    if kind == "M" and n_docs < 2:
        return (f"Tên file bắt đầu bằng M (đa văn bản) nhưng chỉ nhận diện được {n_docs} "
                "văn bản gốc. Cần kiểm tra lại cấu trúc bảng.")
    if kind == "S" and n_docs != 1:
        return (f"Tên file bắt đầu bằng S (đơn văn bản) nhưng nhận diện được {n_docs} "
                "văn bản gốc. Cần kiểm tra lại cấu trúc bảng.")
    return None


# ------------------------------------------------------------------ ghi

def _find_ref_rpr(cell):
    for p in cell.paragraphs:
        for r in p.runs:
            if r._element.rPr is not None:
                return r._element.rPr
    return None


def _set_cell_text(cell, text: str, ref_rpr=None) -> None:
    paras = [ln for ln in nfc(text).split("\n") if ln.strip()]
    if not paras:
        paras = [""]
    first = cell.paragraphs[0]
    for p in cell.paragraphs[1:]:
        p._element.getparent().remove(p._element)
    for r in list(first.runs):
        r._element.getparent().remove(r._element)
    targets = [first] + [cell.add_paragraph() for _ in paras[1:]]
    for p, t in zip(targets, paras):
        run = p.add_run(t)
        if ref_rpr is not None:
            old = run._element.rPr
            if old is not None:
                run._element.remove(old)
            run._element.insert(0, copy.deepcopy(ref_rpr))


def _shade_yellow(cell) -> None:
    tcPr = cell._element.get_or_add_tcPr()
    for old in tcPr.findall(qn("w:shd")):
        tcPr.remove(old)
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), "FFFF00")
    tcPr.append(shd)


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _cmp_text(t: str) -> str:
    return "\n".join(ln.strip() for ln in nfc(t).split("\n") if ln.strip())


def fill_docx(docx_in: str | os.PathLike, docx_out: str | os.PathLike, style: str,
              extract: str, abstract: str, *, protected_dirs: list[Path] | None = None,
              allow_overwrite_content: bool = False) -> dict:
    """Điền 3 hàng cuối, ghi ra docx_out rồi mở lại để xác nhận.

    - Có sẵn 3 hàng nhãn thì điền vào đó; chưa có thì thêm 3 hàng mới và tô vàng.
    - Không bao giờ ghi vào protected_dirs (thư mục input\\) hay ghi đè chính file nguồn.
    """
    src, dst = Path(docx_in), Path(docx_out)
    style = nfc(style).strip()
    extract = nfc(extract).strip()
    abstract = nfc(abstract).strip()
    if style not in STYLES:
        raise DocxError(f"Nhãn '{style}' không hợp lệ. Chọn một trong: {STYLES}")
    if not extract or not abstract:
        raise DocxError("Bản trích chọn hoặc biên soạn đang trống.")
    if dst.resolve() == src.resolve():
        raise DocxError("Không được ghi đè file gốc.")
    for d in protected_dirs or []:
        if _is_inside(dst, d):
            raise DocxError(f"Không được ghi vào thư mục được bảo vệ: {d}")

    doc, table, rows = load_table(src)
    layout = detect_layout(rows)
    if "error" in layout:
        raise DocxError(layout["error"])
    before = [row_text(r) for r in rows]
    if layout["label_rows_with_content"] and not allow_overwrite_content:
        raise DocxError("3 hàng nhãn đã có nội dung ở hàng "
                        f"{[i + 1 for i in layout['label_rows_with_content']]}; "
                        "cần xác nhận ghi đè.")

    ref_rpr = _find_ref_rpr(table.rows[layout["ai_abstract_idx"]].cells[0])
    created = False
    if layout["label_rows_present"]:
        label_cells = [table.rows[i].cells[0] for i in layout["label_idx"]]
    else:
        created = True
        label_cells = []
        for _ in range(3):
            row = table.add_row()
            label_cells.append(row.cells[0])
            _shade_yellow(row.cells[0])
    for cell, text in zip(label_cells, (style, extract, abstract)):
        _set_cell_text(cell, text, ref_rpr)

    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.stem + ".tmp" + dst.suffix)
    doc.save(str(tmp))

    # ---- xác nhận bằng cách mở lại ----
    _, _, rows2 = load_table(tmp)
    after = [row_text(r) for r in rows2]
    n_keep = len(before) - (3 if layout["label_rows_present"] else 0)
    problems = []
    if after[:n_keep] != before[:n_keep]:
        problems.append("Các hàng phía trên bị thay đổi.")
    if len(after) != n_keep + 3:
        problems.append(f"Số hàng bất thường: {len(after)} (cần {n_keep + 3}).")
    else:
        if after[-3] != style:
            problems.append("Hàng phong cách chức năng không khớp.")
        if _cmp_text(after[-2]) != _cmp_text(extract):
            problems.append("Hàng trích chọn không khớp.")
        if _cmp_text(after[-1]) != _cmp_text(abstract):
            problems.append("Hàng biên soạn không khớp.")
    if problems:
        tmp.unlink(missing_ok=True)
        raise DocxError("Xác minh sau khi ghi thất bại: " + " ".join(problems))
    os.replace(tmp, dst)
    return {
        "output": str(dst),
        "rows_kept": n_keep,
        "rows_created": created,
        "style": style,
        "extract_words": count_words(after[-2]),
        "abstract_words": count_words(after[-1]),
    }
