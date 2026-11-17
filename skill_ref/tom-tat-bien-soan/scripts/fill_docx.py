#!/usr/bin/env python3
"""Điền 3 hàng cuối (phong cách chức năng, trích chọn, biên soạn) vào bảng của file .docx.

Cách dùng:
    python fill_docx.py IN.docx OUT.docx --style "<nhãn>" --extract extract.txt --abstract abstract.txt

- Nếu file đã có sẵn 3 hàng nhãn thì điền vào đó (ghi đè nội dung cũ).
- Nếu chưa có thì thêm 3 hàng mới ở cuối bảng và tô vàng.
- Các hàng khác được giữ nguyên; script mở lại file để xác nhận.
"""
import argparse
import copy
import os
import sys

from docx.oxml import OxmlElement
from docx.oxml.ns import qn

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import STYLES, count_words, detect_layout, load_table, nfc, row_text  # noqa: E402


def find_ref_rpr(cell):
    for p in cell.paragraphs:
        for r in p.runs:
            if r._element.rPr is not None:
                return r._element.rPr
    return None


def set_cell_text(cell, text, ref_rpr=None):
    paras = [ln for ln in nfc(text).split("\n") if ln.strip()]
    if not paras:
        paras = [""]
    first = cell.paragraphs[0]
    for p in cell.paragraphs[1:]:
        p._element.getparent().remove(p._element)
    first.text = ""
    targets = [first] + [cell.add_paragraph() for _ in paras[1:]]
    for p, t in zip(targets, paras):
        run = p.add_run(t)
        if ref_rpr is not None:
            old = run._element.rPr
            if old is not None:
                run._element.remove(old)
            run._element.insert(0, copy.deepcopy(ref_rpr))


def shade_yellow(cell):
    tcPr = cell._element.get_or_add_tcPr()
    for old in tcPr.findall(qn("w:shd")):
        tcPr.remove(old)
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), "FFFF00")
    tcPr.append(shd)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("docx_in")
    ap.add_argument("docx_out")
    ap.add_argument("--style", required=True)
    ap.add_argument("--extract", required=True)
    ap.add_argument("--abstract", required=True)
    args = ap.parse_args()

    style = args.style.strip()
    if style not in STYLES:
        sys.exit(f"LỖI: nhãn '{style}' không hợp lệ. Chọn một trong: {STYLES}")
    extract = open(args.extract, encoding="utf-8").read().strip()
    abstract = open(args.abstract, encoding="utf-8").read().strip()
    if not extract or not abstract:
        sys.exit("LỖI: file trích chọn hoặc biên soạn đang trống.")

    doc, table, rows = load_table(args.docx_in)
    layout = detect_layout(rows)
    if "error" in layout:
        sys.exit("LỖI: " + layout["error"])
    before = [row_text(r) for r in rows]

    ref_rpr = find_ref_rpr(table.rows[layout["ai_abstract_idx"]].cells[0])

    if layout["label_rows_present"]:
        idx = layout["label_idx"]
        for i in idx:
            if before[i].strip() and before[i].strip().lower() not in STYLES:
                print(f"CẢNH BÁO: ghi đè nội dung có sẵn ở hàng {i + 1}.")
        label_cells = [table.rows[i].cells[0] for i in idx]
    else:
        label_cells = []
        for _ in range(3):
            row = table.add_row()
            label_cells.append(row.cells[0])
            shade_yellow(row.cells[0])

    for cell, text in zip(label_cells, (style, extract, abstract)):
        set_cell_text(cell, text, ref_rpr)

    os.makedirs(os.path.dirname(os.path.abspath(args.docx_out)), exist_ok=True)
    doc.save(args.docx_out)

    # ---- xác nhận ----
    _, _, rows2 = load_table(args.docx_out)
    after = [row_text(r) for r in rows2]
    n_before_keep = len(before) - (3 if layout["label_rows_present"] else 0)
    assert after[:n_before_keep] == before[:n_before_keep], "Các hàng phía trên bị thay đổi!"
    assert len(after) == n_before_keep + 3, f"Số hàng bất thường: {len(after)}"
    assert after[-3] == style, "Hàng phong cách không khớp"
    print(f"Đã ghi {args.docx_out}")
    print(f"  Hàng {len(after) - 2}: phong cách chức năng = {after[-3]}")
    print(f"  Hàng {len(after) - 1}: trích chọn  ({count_words(after[-2])} từ)")
    print(f"  Hàng {len(after)}: biên soạn ({count_words(after[-1])} từ)")
    print(f"  {n_before_keep} hàng phía trên giữ nguyên.")


if __name__ == "__main__":
    main()
