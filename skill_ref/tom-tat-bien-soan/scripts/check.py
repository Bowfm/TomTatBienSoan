#!/usr/bin/env python3
"""Kiểm tra cơ học bản trích chọn và bản biên soạn trước khi điền vào file.

Cách dùng:
    python check.py WORK/units.json --style "báo chí - công luận" \
        --extract WORK/extract.txt --abstract WORK/abstract.txt

Thoát với mã 1 nếu còn LỖI.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (COMPRESS_MAX, COMPRESS_MIN, COMPRESS_TARGET_HIGH,  # noqa: E402
                    COMPRESS_TARGET_LOW, RATIO_LIMIT, STYLES, count_words,
                    norm, nfc, split_sentences)

BULLET_RE = re.compile(r"^\s*([-•*–—+·▪●○■]|\d+[.)]|[a-zA-Z][.)])\s+")
errors, warns, infos = [], [], []


def err(m):
    errors.append(m)


def warn(m):
    warns.append(m)


def info(m):
    infos.append(m)


def lines_of(text):
    return [ln for ln in nfc(text).split("\n") if ln.strip()]


def is_heading_like(line):
    s = line.strip()
    return count_words(s) <= 14 and not re.search(r"[.!?…\"”)]$", s)


def bigrams(text):
    toks = re.findall(r"\w+", nfc(text).lower())
    return list(zip(toks, toks[1:]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("units_json")
    ap.add_argument("--style", required=True)
    ap.add_argument("--extract", required=True)
    ap.add_argument("--abstract", required=True)
    args = ap.parse_args()

    data = json.load(open(args.units_json, encoding="utf-8"))
    extract = open(args.extract, encoding="utf-8").read()
    abstract = open(args.abstract, encoding="utf-8").read()
    style = args.style.strip()

    # ---- nhãn ----
    if style not in STYLES:
        err(f"Nhãn '{style}' không thuộc 5 giá trị hợp lệ: {STYLES}")

    # ---- độ dài ----
    total = data["budget"]["total_words"]
    n_ext, n_abs = count_words(extract), count_words(abstract)
    compress = n_ext / total if total else 0
    print(f"Số từ: gốc {total} | trích chọn {n_ext} | biên soạn {n_abs}")
    print(f"Compress (trích chọn / gốc) = {compress:.3f}")
    if compress < COMPRESS_MIN or compress > COMPRESS_MAX:
        err(f"Compress {compress:.3f} ngoài khoảng bắt buộc [0,1; 0,5].")
    elif compress < COMPRESS_TARGET_LOW or compress > COMPRESS_TARGET_HIGH:
        warn(f"Compress {compress:.3f} đạt nhưng sát biên (nên 0,20-0,40) - "
             "cách đếm từ của hệ thống chấm có thể lệch nhẹ.")

    if n_ext and n_abs:
        if n_abs < n_ext:
            r = n_abs / n_ext
            edit = 1 - r
            print(f"Biên soạn ngắn hơn trích chọn: B/T = {r:.3f} (cần ≤ {RATIO_LIMIT}), Edit = {edit:.3f}")
            if r > RATIO_LIMIT:
                err(f"B/T = {r:.3f} > {RATIO_LIMIT}: bản biên soạn cần ngắn hơn nữa "
                    f"(tối đa {int(n_ext * RATIO_LIMIT)} từ) hoặc dài hơn bản trích chọn đủ nhiều.")
        else:
            r = n_ext / n_abs
            edit = 1 - r
            print(f"Biên soạn dài hơn trích chọn: T/B = {r:.3f} (cần ≤ {RATIO_LIMIT}), Edit = {edit:.3f}")
            if r > RATIO_LIMIT:
                err(f"T/B = {r:.3f} > {RATIO_LIMIT}: bản biên soạn cần dài hơn nữa "
                    f"(tối thiểu {int(n_ext / RATIO_LIMIT) + 1} từ) hoặc ngắn hơn bản trích chọn.")
        if edit < 0.1:
            err(f"Edit = {edit:.3f} < 0,1.")
        if n_abs < 0.4 * n_ext:
            warn(f"Bản biên soạn chỉ bằng {n_abs / n_ext:.0%} bản trích chọn - quá ngắn, "
                 "dễ mất ý chính. Nên khoảng 55%-68%.")
    else:
        err("Bản trích chọn hoặc bản biên soạn đang trống.")

    # ---- bản trích chọn: nguyên văn và đúng thứ tự ----
    source = " ".join(norm(u["text"]) for d in data["docs"] for u in d["units"])
    pos, not_verbatim, out_of_order = 0, [], []
    for ln in lines_of(extract):
        for s in split_sentences(ln):
            s = norm(s)
            idx = source.find(s, pos)
            if idx >= 0:
                pos = idx + len(s)
                continue
            if source.find(s) >= 0:
                out_of_order.append(s)
            else:
                not_verbatim.append(s)
    for s in not_verbatim[:5]:
        err("Bản trích chọn có câu không phải nguyên văn bản gốc: “" + s[:90] + "…”")
    for s in out_of_order[:5]:
        err("Bản trích chọn sai thứ tự so với bản gốc: “" + s[:90] + "…”")
    if len(not_verbatim) > 5 or len(out_of_order) > 5:
        err("... còn nhiều câu khác cùng lỗi.")

    # ---- định dạng bản biên soạn theo phong cách ----
    a_lines = lines_of(abstract)
    bullets = [ln for ln in a_lines if BULLET_RE.match(ln)]
    if style in ("sinh hoạt hằng ngày", "báo chí - công luận", "chính luận") and bullets:
        err(f"Phong cách '{style}' không được dùng bullet/gạch đầu dòng/đánh số đầu dòng "
            f"({len(bullets)} dòng, ví dụ: “{bullets[0].strip()[:60]}”).")
    if style == "báo chí - công luận":
        colon = [ln for ln in a_lines if ln.rstrip().endswith(":")]
        if colon:
            err("Báo chí: có dòng kết thúc bằng dấu hai chấm rồi xuống dòng "
                f"(“{colon[0].strip()[:60]}”).")
    heads = [ln for ln in a_lines if is_heading_like(ln)]
    if style == "sinh hoạt hằng ngày" and heads:
        err("Sinh hoạt hằng ngày: không dùng tiêu đề; dòng sau trông như tiêu đề: "
            f"“{heads[0].strip()[:60]}”.")
    if style == "chính luận":
        if len(a_lines) > 2 and not re.search(r"\bMột là\b", abstract):
            info("Chính luận: bản biên soạn chưa có mẫu “Một là..., Hai là...”. "
                 "Nếu bản gốc nêu nhiều chủ trương/nhiệm vụ thì nên dùng mẫu này.")
        if not heads and len(data["docs"]) > 1:
            info("Đa văn bản chính luận: nên có tiêu đề cho từng phần.")
    if style in ("hành chính - công vụ", "khoa học", "báo chí - công luận") \
            and len(data["docs"]) > 1 and not heads:
        info("Đa văn bản: nên có tiêu đề để văn bản mạch lạc.")

    # ---- chính tả/định dạng cơ học ----
    if re.search(r"[ \t]{2,}", abstract):
        warn("Bản biên soạn có khoảng trắng kép.")
    if re.search(r"\s[,.;:!?]", abstract):
        warn("Bản biên soạn có khoảng trắng trước dấu câu.")
    if abstract.count("(") != abstract.count(")"):
        warn("Ngoặc đơn không cân bằng trong bản biên soạn.")
    if (abstract.count('"') % 2) or (abstract.count("“") != abstract.count("”")):
        warn("Dấu ngoặc kép không cân bằng trong bản biên soạn.")
    dec = re.findall(r"(?<![\d.,])\d+\.\d{1,2}(?!\d|\.\d)", abstract)
    if dec:
        warn("Có thể đã viết dấu phẩy thập phân thành dấu chấm: " + ", ".join(dec[:8])
             + " (bỏ qua nếu là số phiên bản hay mã số).")

    # ---- số liệu phải có trong bản gốc ----
    src_plain = nfc(" ".join(u["text"] for d in data["docs"] for u in d["units"]))
    nums = {m.rstrip(".,") for m in re.findall(r"\d+(?:[.,]\d+)*", nfc(abstract))}
    missing = sorted(n for n in nums if n not in src_plain)
    if missing:
        warn("Số liệu trong bản biên soạn không thấy trong bản gốc (làm tròn, đổi dạng "
             "hoặc bịa?): " + ", ".join(missing[:15]))

    # ---- mức độ bám bản gốc (chỉ báo thô liên quan BARTScore) ----
    src_bi = set(bigrams(src_plain))
    ab_bi = bigrams(abstract)
    if ab_bi:
        cover = sum(1 for b in ab_bi if b in src_bi) / len(ab_bi)
        print(f"Độ bám bản gốc (tỉ lệ cặp từ của bản biên soạn có trong bản gốc) = {cover:.2f}")
        if cover < 0.35:
            warn("Bản biên soạn khá xa từ ngữ bản gốc; nguy cơ BARTScore thấp. "
                 "Hãy dùng lại thuật ngữ và cụm từ của bản gốc ở chỗ không cần đổi.")

    # ---- báo cáo ----
    print()
    for tag, items in (("LỖI", errors), ("CẢNH BÁO", warns), ("GHI CHÚ", infos)):
        for m in items:
            print(f"[{tag}] {m}")
    if not errors:
        print("\nKẾT QUẢ: không có lỗi cơ học. Hãy tự đọc lại bản biên soạn với văn bản gốc "
              "để soát chủ thể, sắc thái, lập trường, số liệu.")
    else:
        print(f"\nKẾT QUẢ: còn {len(errors)} lỗi cần sửa.")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
