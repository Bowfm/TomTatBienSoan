"""Kiểm tra cơ học bản biên soạn: tỷ lệ độ dài, phong cách, số liệu, BARTScore, G-Eval."""
from __future__ import annotations

import re

from ..textutil import bigrams, count_words, lines_of, nfc
from ..units import abstract_targets
from .core import ERROR, INFO, WARNING, CheckResult, is_heading_like
from .scoring import estimate_bartscore, estimate_geval_heuristic

BULLET_RE = re.compile(r"^\s*([-•*–—+·▪●○■►✓]|\d+[.)/]|[a-zA-Zđ][.)])\s+")
NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
DEC_POINT_RE = re.compile(r"(?<![\d.,])\d+\.\d{1,2}(?!\d|\.\d)")
NO_BULLET_STYLES = ("sinh hoạt hằng ngày", "báo chí - công luận", "chính luận")

# Cụm từ dẫn dắt nối đoạn cấm dùng trong biên soạn đa văn bản (mỗi bài là độc lập)
FORBIDDEN_TRANSITIONS_RE = re.compile(
    r"^(?:"
    r"(?:Trong|Ở|Tại)\s+(?:một\s+)?(?:chia sẻ|bài viết|bài đăng|văn bản|nội dung|trường hợp|nghiên cứu|thông tin|câu chuyện)\s+khác,?\s*"
    r"|(?:Một\s+)?(?:chia sẻ|bài viết|bài đăng)\s+khác(?:\s+(?:cho biết|cho rằng|nêu rõ|chia sẻ|nhấn mạnh)),?\s*"
    r"|(?:Theo\s+(?:một\s+)?(?:chia sẻ|bài viết|bài đăng)\s+khác,?\s*)"
    r"|(?:Bên cạnh đó|Mặt khác|Ngoài ra|Đồng thời|Mặt khác nữa|Song song đó|Tiếp theo|Cùng với đó|Ở một khía cạnh khác),?\s*"
    r"|(?:Đối với|Về phía)\s+(?:văn bản|bài viết|nội dung)\s+(?:thứ\s+\w+|\d+|này|kia),?\s*"
    r"|(?:Văn bản|Văn bản số|Bài|Bài số)\s+\d+\s*[:–-]\s*"
    r"|(?:Trong khi đó),?\s*"
    r")",
    re.IGNORECASE | re.UNICODE,
)


def clean_multi_doc_abstract(abstract: str, n_docs: int = 1) -> str:
    """Chuẩn hóa bản biên soạn đa văn bản:
    1. Giữa các đoạn tóm tắt của từng văn bản để trống 1 dòng (\\n\\n).
    2. Loại bỏ câu nối/từ dẫn dắt giữa các đoạn và viết hoa trực tiếp nội dung văn bản.
    """
    if not abstract:
        return ""
    paras = [p.strip() for p in re.split(r"\n+", nfc(abstract).strip()) if p.strip()]
    if not paras:
        return ""
    if n_docs > 1:
        cleaned_paras: list[str] = []
        for p in paras:
            cleaned = FORBIDDEN_TRANSITIONS_RE.sub("", p).strip()
            if cleaned:
                cleaned = cleaned[0].upper() + cleaned[1:]
            cleaned_paras.append(cleaned)
        return "\n\n".join(cleaned_paras)
    return "\n".join(paras)


def check_length(n_ext: int, n_abs: int, cfg: dict, res: CheckResult) -> None:
    a, g = cfg["abstract"], cfg["grading"]
    lim = a["ratio_limit"]
    t = abstract_targets(n_ext, cfg)
    res.metrics["abstract_targets"] = t
    if not (n_ext and n_abs):
        return
    if n_abs < n_ext:
        r = n_abs / n_ext
        res.metrics.update(direction="shorter", ratio=round(r, 4), ratio_name="B/T", edit=round(1 - r, 4))
        if r > lim:
            if t["allow_longer"]:
                hint = (f"cần cắt ít nhất {n_abs - t['short_max']} từ (tối đa {t['short_max']} từ) "
                        f"hoặc viết dài hơn, tối thiểu {t['long_min']} từ")
            else:
                hint = (f"cần cắt ít nhất {n_abs - t['short_max']} từ (tối đa {t['short_max']} từ, "
                        f"nên {t['short_low']}-{t['short_high']} từ)")
            res.add(ERROR, "ratio",
                    f"Bản biên soạn {n_abs} từ = {r:.1%} bản trích chọn ({n_ext} từ), vượt "
                    f"{lim:.0%}: {hint}.")
        if r < a["too_short_warn"]:
            res.add(WARNING, "too_short",
                    f"Bản biên soạn chỉ bằng {r:.0%} bản trích chọn - quá ngắn, dễ mất ý chính "
                    f"(nên {t['short_low']}-{t['short_high']} từ).")
    else:
        r = n_ext / n_abs
        res.metrics.update(direction="longer", ratio=round(r, 4), ratio_name="T/B", edit=round(1 - r, 4))
        if r > lim:
            res.add(ERROR, "ratio",
                    f"Bản biên soạn {n_abs} từ, không ngắn hơn bản trích chọn ({n_ext} từ) đủ nhiều: "
                    f"cần cắt xuống tối đa {t['short_max']} từ (cắt ít nhất {n_abs - t['short_max']} từ)"
                    + (f" hoặc viết dài tối thiểu {t['long_min']} từ." if t["allow_longer"] else "."))
        elif not t["allow_longer"]:
            res.add(WARNING, "longer_not_preferred",
                    f"Bản biên soạn dài hơn bản trích chọn (T/B = {r:.2f}); hợp lệ theo quy tắc 70% "
                    f"nhưng cấu hình ưu tiên viết ngắn hơn ({t['short_low']}-{t['short_high']} từ).")
    if res.metrics.get("edit", 1) < g["edit_min"]:
        res.add(ERROR, "edit", f"Edit = {res.metrics['edit']:.3f} < {g['edit_min']}.")


def check_abstract(data: dict, style: str, extract: str, abstract: str, cfg: dict,
                   res: CheckResult) -> None:
    mode = cfg.get("word_count", {}).get("mode", "whitespace_alnum")
    n_ext = count_words(extract, mode)
    n_abs = count_words(abstract, mode)
    res.metrics["abstract_words"] = n_abs
    if not abstract.strip():
        res.add(ERROR, "abstract_empty", "Bản biên soạn đang trống.")
        res.metrics["abstract_targets"] = abstract_targets(n_ext, cfg)
        return
    check_length(n_ext, n_abs, cfg, res)

    a_lines = lines_of(abstract)
    # markdown không hợp lệ trong ô Word
    md = [ln for ln in a_lines if re.match(r"^\s*#{1,6}\s", ln) or "**" in ln or "__" in ln]
    if md:
        res.add(ERROR, "markdown",
                f"Có ký hiệu markdown (#, **) không hợp lệ trong ô Word: “{md[0].strip()[:60]}”.")
    bullets = [ln for ln in a_lines if BULLET_RE.match(ln)]
    if style in NO_BULLET_STYLES and bullets:
        res.add(ERROR, "bullet",
                f"Phong cách '{style}' không được dùng bullet/gạch đầu dòng/đánh số đầu dòng "
                f"({len(bullets)} dòng, ví dụ: “{bullets[0].strip()[:60]}”).")
    if style == "báo chí - công luận":
        colon = [ln for ln in a_lines if ln.rstrip().endswith(":")]
        if colon:
            res.add(ERROR, "colon_newline",
                    f"Báo chí: có dòng kết thúc bằng dấu hai chấm rồi xuống dòng (“{colon[0].strip()[:60]}”).")
    heads = [ln for ln in a_lines if is_heading_like(ln) and not BULLET_RE.match(ln)]
    if style == "sinh hoạt hằng ngày" and heads:
        res.add(ERROR, "heading",
                f"Sinh hoạt hằng ngày: không dùng tiêu đề; dòng sau trông như tiêu đề: “{heads[0].strip()[:60]}”.")
    if style == "chính luận" and len(a_lines) > 2 and not re.search(r"\bMột là\b", abstract):
        res.add(INFO, "chinh_luan_list",
                "Chính luận: chưa có mẫu “Một là..., Hai là...”. Nếu bản gốc nêu nhiều chủ trương/"
                "nhiệm vụ thì nên dùng mẫu này.")
    if style != "sinh hoạt hằng ngày" and len(data["docs"]) > 1 and not heads:
        res.add(INFO, "multi_heading", "Đa văn bản: nên có tiêu đề để văn bản mạch lạc.")

    # Đa văn bản: giữa các đoạn tóm tắt của từng văn bản phải để trống 1 dòng, không dùng từ nối dẫn dắt
    if len(data.get("docs", [])) > 1:
        non_empty_paras = [ln.strip() for ln in abstract.split("\n") if ln.strip()]
        if len(non_empty_paras) > 1 and "\n\n" not in abstract:
            res.add(
                WARNING,
                "multi_doc_no_blank_line",
                "Đa văn bản: giữa các đoạn tóm tắt của từng văn bản cần để trống 1 dòng (dòng trắng phân cách) "
                "để các bài độc lập nhau.",
                "abstract",
            )
        for idx, p in enumerate(non_empty_paras):
            m = FORBIDDEN_TRANSITIONS_RE.match(p)
            if m:
                lead = m.group(0).strip()
                res.add(
                    ERROR,
                    "multi_doc_transition",
                    f"Đa văn bản: đoạn {idx + 1} có cụm từ dẫn dắt “{lead}”. "
                    "Mỗi bài ở file đa văn bản là độc lập, không thêm câu nối dẫn dắt mà đâm thẳng vào tóm tắt nội dung.",
                    "abstract",
                )

    # chính tả / định dạng cơ học
    if re.search(r"[ \t]{2,}", abstract):
        res.add(WARNING, "double_space", "Bản biên soạn có khoảng trắng kép.")
    if re.search(r"\s[,.;:!?](?!\.)", abstract):
        res.add(WARNING, "space_before_punct", "Bản biên soạn có khoảng trắng trước dấu câu.")
    if abstract.count("(") != abstract.count(")"):
        res.add(WARNING, "paren", "Ngoặc đơn không cân bằng trong bản biên soạn.")
    if (abstract.count('"') % 2) or (abstract.count("“") != abstract.count("”")):
        res.add(WARNING, "quotes", "Dấu ngoặc kép không cân bằng trong bản biên soạn.")

    src_plain = nfc(" ".join(u["text"] for d in data["docs"] for u in d["units"]))
    abs_n = nfc(abstract)
    decs = DEC_POINT_RE.findall(abs_n)
    hard = [x for x in decs if x.replace(".", ",") in src_plain and x not in src_plain]
    soft = [x for x in decs if x not in hard and x not in src_plain]
    if hard:
        res.add(ERROR, "decimal_point",
                "Viết dấu chấm thập phân trong khi bản gốc dùng dấu phẩy: "
                + ", ".join(f"{x} (gốc: {x.replace('.', ',')})" for x in hard[:8]) + ".")
    if soft:
        res.add(WARNING, "decimal_point_suspect",
                "Có thể đã viết dấu phẩy thập phân thành dấu chấm: " + ", ".join(soft[:8])
                + " (bỏ qua nếu là số phiên bản hay mã số).")
    nums = {m.rstrip(".,") for m in NUM_RE.findall(abs_n)}
    missing = sorted(x for x in nums if x not in src_plain and x not in hard)
    if missing:
        res.add(WARNING, "number_missing",
                "Số liệu trong bản biên soạn không có trong bản gốc (làm tròn, đổi dạng hoặc bịa?): "
                + ", ".join(missing[:15]) + ".")

    src_bi = set(bigrams(src_plain))
    ab_bi = bigrams(abstract)
    if ab_bi:
        cover = sum(1 for b in ab_bi if b in src_bi) / len(ab_bi)
        res.metrics["coverage"] = round(cover, 3)
        if cover < cfg["abstract"]["coverage_warn"]:
            res.add(WARNING, "low_coverage",
                    f"Bản biên soạn khá xa từ ngữ bản gốc (độ bám {cover:.2f}); nguy cơ BARTScore thấp. "
                    "Hãy dùng lại thuật ngữ và cụm từ của bản gốc ở chỗ không cần đổi.")

    # Ước lượng BARTScore (ngưỡng >= -2.56)
    bart_min = float(cfg["grading"].get("bartscore_min", -2.56))
    bart_est = estimate_bartscore(src_plain, extract, abstract, missing_nums=len(missing))
    res.metrics["bartscore_est"] = bart_est
    if bart_est < bart_min:
        res.add(
            ERROR,
            "bartscore_low",
            f"BARTScore ước lượng = {bart_est:.2f} < ngưỡng {bart_min:.2f}: từ ngữ bản biên soạn đang xa bản gốc. "
            "Cần giữ nguyên các thuật ngữ, cụm từ cốt lõi của văn bản gốc và bản trích chọn, không thay từ đồng nghĩa tùy tiện.",
            "abstract",
        )

    # Ước lượng G-Eval (ngưỡng >= 4.25)
    geval_min = float(cfg["grading"].get("geval_min", 4.25))
    geval_est = estimate_geval_heuristic(res, bart_est, res.metrics.get("rouge2_est", 40.0))
    res.metrics["geval_est"] = geval_est
    if geval_est < geval_min:
        res.add(
            ERROR,
            "geval_low",
            f"G-Eval ước lượng = {geval_est:.2f} < ngưỡng {geval_min:.2f}: cần khắc phục các lỗi định dạng/độ dài/độ bám bản gốc để nâng điểm G-Eval.",
            "abstract",
        )
