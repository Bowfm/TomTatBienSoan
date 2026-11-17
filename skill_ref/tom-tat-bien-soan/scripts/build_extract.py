#!/usr/bin/env python3
"""Ghép bản trích chọn từ các đơn vị (câu/đề mục) đã chọn.

Cách dùng:
    python build_extract.py WORK/units.json WORK/spec.txt -o WORK/extract.txt

spec.txt: mỗi dòng `<số văn bản>: <id hoặc khoảng id, cách nhau dấu phẩy>`, ví dụ
    1: 1-3, 6, 9-11
    2: 2, 4-5
Dòng bắt đầu bằng # là chú thích. Script luôn sắp xếp theo thứ tự gốc.
"""
import argparse
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (COMPRESS_MAX, COMPRESS_MIN, COMPRESS_TARGET_HIGH,  # noqa: E402
                    COMPRESS_TARGET_LOW, RATIO_LIMIT, count_words)


def parse_ids(s):
    ids = set()
    for part in re.split(r"[,\s]+", s.strip()):
        if not part:
            continue
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if a > b:
                a, b = b, a
            ids.update(range(a, b + 1))
        elif part.isdigit():
            ids.add(int(part))
        else:
            sys.exit(f"LỖI: không hiểu '{part}' trong spec.")
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("units_json")
    ap.add_argument("spec")
    ap.add_argument("-o", "--out", required=True)
    args = ap.parse_args()

    data = json.load(open(args.units_json, encoding="utf-8"))
    docs = {d["n"]: d for d in data["docs"]}
    chosen = {}
    for ln in open(args.spec, encoding="utf-8"):
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        m = re.fullmatch(r"(\d+)\s*:\s*(.+)", ln)
        if not m:
            sys.exit(f"LỖI: dòng spec sai định dạng: '{ln}'")
        n = int(m.group(1))
        if n not in docs:
            sys.exit(f"LỖI: không có văn bản số {n} (có {sorted(docs)}).")
        chosen.setdefault(n, set()).update(parse_ids(m.group(2)))

    out_lines, per_doc = [], {}
    for n in sorted(chosen):
        units = {u["id"]: u for u in docs[n]["units"]}
        bad = sorted(i for i in chosen[n] if i not in units)
        if bad:
            sys.exit(f"LỖI: văn bản {n} không có đơn vị số {bad}.")
        cur_para, cur = None, []
        words = 0
        for i in sorted(chosen[n]):
            u = units[i]
            words += u["words"]
            if cur_para is not None and u["para"] != cur_para:
                out_lines.append(" ".join(cur))
                cur = []
            cur_para = u["para"]
            cur.append(u["text"])
        if cur:
            out_lines.append(" ".join(cur))
        per_doc[n] = words

    text = "\n".join(out_lines) + "\n"
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text)

    total = data["budget"]["total_words"]
    n_ext = count_words(text)
    ratio = n_ext / total if total else 0
    print(f"Đã ghi {args.out}")
    for n in sorted(per_doc):
        print(f"  Văn bản {n}: {per_doc[n]} / {docs[n]['words']} từ "
              f"({per_doc[n] / docs[n]['words']:.0%})")
    print(f"Bản trích chọn: {n_ext} từ / {total} từ gốc = {ratio:.1%}")
    if ratio < COMPRESS_MIN or ratio > COMPRESS_MAX:
        print("  LỖI: ngoài khoảng bắt buộc 10% - 50%.")
    elif ratio < COMPRESS_TARGET_LOW or ratio > COMPRESS_TARGET_HIGH:
        print("  CẢNH BÁO: đạt yêu cầu nhưng sát biên; nên đưa về 20% - 40% (lý tưởng ~30%).")
    else:
        print("  OK: nằm trong khoảng nhắm tới.")
    short_hi = math.floor(n_ext * RATIO_LIMIT)
    short_lo = round(n_ext * 0.55)
    long_lo = math.ceil(n_ext / RATIO_LIMIT)
    print("Độ dài bản biên soạn cần đạt (một trong hai):")
    print(f"  - ngắn hơn: tối đa {short_hi} từ (nên {short_lo} - {short_hi} từ)")
    print(f"  - dài hơn : tối thiểu {long_lo} từ")


if __name__ == "__main__":
    main()
