#!/usr/bin/env python3
"""Đọc file .docx của dự án, nhận diện các hàng và chia văn bản gốc thành câu.

Cách dùng:
    python inspect_docx.py FILE.docx --workdir WORK

Ghi ra WORK/units.json và WORK/units.txt, in tóm tắt cấu trúc và ngân sách số từ.
"""
import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (COMPRESS_MAX, COMPRESS_MIN, COMPRESS_TARGET_HIGH,  # noqa: E402
                    COMPRESS_TARGET_LOW, build_units, count_words,
                    detect_layout, load_table, row_text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("docx")
    ap.add_argument("--workdir", required=True)
    args = ap.parse_args()

    _, _, rows = load_table(args.docx)
    layout = detect_layout(rows)
    if "error" in layout:
        sys.exit("LỖI: " + layout["error"])

    os.makedirs(args.workdir, exist_ok=True)

    query = row_text(rows[0]) if layout["has_query"] else ""
    docs = []
    for k, ri in enumerate(layout["doc_idx"], start=1):
        units = build_units(rows[ri])
        docs.append({
            "n": k, "row": ri,
            "words": sum(u["words"] for u in units),
            "units": units,
        })
    total = sum(d["words"] for d in docs)
    ai_extract = row_text(rows[layout["ai_extract_idx"]])
    ai_abstract = row_text(rows[layout["ai_abstract_idx"]])

    lo = math.ceil(total * COMPRESS_MIN)
    hi = math.floor(total * COMPRESS_MAX)
    budget = {
        "total_words": total,
        "extract_min": lo,
        "extract_max": hi,
        "extract_target_low": round(total * COMPRESS_TARGET_LOW),
        "extract_target_high": round(total * COMPRESS_TARGET_HIGH),
        "extract_target_mid": round(total * 0.30),
    }

    with open(os.path.join(args.workdir, "units.json"), "w", encoding="utf-8") as f:
        json.dump({"query": query, "layout": layout, "docs": docs,
                   "ai_extract": ai_extract, "ai_abstract": ai_abstract,
                   "budget": budget}, f, ensure_ascii=False, indent=1)

    lines = ["=== QUERY ===", query or "(không có query)", ""]
    for d in docs:
        lines.append(f"=== VĂN BẢN {d['n']} ({d['words']} từ, {len(d['units'])} đơn vị) ===")
        last_para = None
        for u in d["units"]:
            if last_para is not None and u["para"] != last_para:
                lines.append("")
            last_para = u["para"]
            lines.append(f"[{u['id']}] ({u['words']} từ) {u['text']}")
        lines.append("")
    lines.append(f"=== GỢI Ý TRÍCH CHỌN CỦA AI ({count_words(ai_extract)} từ) - chỉ tham khảo ===")
    lines.append(ai_extract or "(trống)")
    lines.append("")
    lines.append(f"=== GỢI Ý BIÊN SOẠN CỦA AI ({count_words(ai_abstract)} từ) - chỉ tham khảo ===")
    lines.append(ai_abstract or "(trống)")
    with open(os.path.join(args.workdir, "units.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print("NHẬN DIỆN CẤU TRÚC")
    print(f"  Tổng số hàng trong bảng : {layout['n_rows']}")
    print(f"  Query                   : {'có' if layout['has_query'] else 'không có / trống'}")
    kind = "đa văn bản" if len(docs) > 1 else "đơn văn bản"
    print(f"  Văn bản gốc             : {len(docs)} ({kind}), hàng {[d['row'] + 1 for d in docs]}")
    print(f"  Gợi ý trích chọn của AI : hàng {layout['ai_extract_idx'] + 1}")
    print(f"  Gợi ý biên soạn của AI  : hàng {layout['ai_abstract_idx'] + 1}")
    if layout["label_rows_present"]:
        print(f"  3 hàng nhãn             : đã có sẵn, hàng {[i + 1 for i in layout['label_idx']]} "
              "(sẽ bị ghi đè nếu đã có nội dung)")
    else:
        print("  3 hàng nhãn             : chưa có, fill_docx.py sẽ thêm vào cuối bảng")
    for n in layout["notes"]:
        print("  LƯU Ý:", n)
    for d in docs:
        print(f"  - Văn bản {d['n']}: {d['words']} từ")
    print()
    print("NGÂN SÁCH SỐ TỪ")
    print(f"  Tổng số từ các văn bản gốc : {total}")
    print(f"  Bản trích chọn bắt buộc    : {lo} - {hi} từ (10% - 50%)")
    print(f"  Khoảng nhắm tới (20%-40%)  : {budget['extract_target_low']} - "
          f"{budget['extract_target_high']} từ, lý tưởng ~{budget['extract_target_mid']} từ (30%)")
    print()
    print(f"Đã ghi {os.path.join(args.workdir, 'units.txt')} và units.json. Hãy đọc units.txt.")


if __name__ == "__main__":
    main()
