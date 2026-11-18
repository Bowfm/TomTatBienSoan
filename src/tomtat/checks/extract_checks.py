"""Kiểm tra cơ học bản trích chọn: nén, nguyên văn, đúng thứ tự, ROUGE-2."""
from __future__ import annotations

from ..textutil import count_words, lines_of, norm, split_sentences
from .core import ERROR, WARNING, CheckResult
from .scoring import estimate_rouge2


def _source_index(data: dict):
    """Chuỗi nguồn nối các đơn vị + bản đồ vị trí -> đơn vị."""
    parts, spans = [], []
    pos = 0
    for d in data["docs"]:
        for u in d["units"]:
            t = norm(u["text"])
            if parts:
                pos += 1  # khoảng trắng nối
            spans.append((pos, pos + len(t), d.get("n", 1), u.get("id", 1)))
            parts.append(t)
            pos += len(t)
    return " ".join(parts), spans


def check_extract(data: dict, extract: str, cfg: dict, res: CheckResult) -> None:
    g, e = cfg["grading"], cfg["extract"]
    mode = cfg.get("word_count", {}).get("mode", "whitespace_alnum")
    total = data["budget"]["total_words"]
    n_ext = count_words(extract, mode)
    compress = n_ext / total if total else 0.0
    res.metrics.update(total_words=total, extract_words=n_ext, compress=round(compress, 4))
    if not extract.strip():
        res.add(ERROR, "extract_empty", "Bản trích chọn đang trống.", "extract")
        return
    lo, hi = data["budget"]["extract_min"], data["budget"]["extract_max"]
    mid = data["budget"]["extract_target_mid"]
    if compress > g["compress_max"]:
        res.add(
            ERROR,
            "compress_high",
            f"Bản trích chọn {n_ext} từ = {compress:.1%} bản gốc ({total} từ), vượt "
            f"{g['compress_max']:.0%}: cần bớt ít nhất {n_ext - hi} từ (nên bớt khoảng "
            f"{n_ext - mid} từ để về ~{e['target']:.0%}).",
            "extract",
        )
    elif compress < g["compress_min"]:
        res.add(
            ERROR,
            "compress_low",
            f"Bản trích chọn {n_ext} từ = {compress:.1%} bản gốc ({total} từ), dưới "
            f"{g['compress_min']:.0%}: cần thêm ít nhất {lo - n_ext} từ (nên thêm khoảng "
            f"{mid - n_ext} từ để về ~{e['target']:.0%}).",
            "extract",
        )
    elif compress < e["target_low"] or compress > e["target_high"]:
        delta = mid - n_ext
        how = f"thêm khoảng {delta} từ" if delta > 0 else f"bớt khoảng {-delta} từ"
        res.add(
            WARNING,
            "compress_edge",
            f"Compress {compress:.3f} đạt nhưng sát biên (nên {e['target_low']:.2f}-"
            f"{e['target_high']:.2f}); {how} để về ~{e['target']:.0%}.",
            "extract",
        )

    # nguyên văn và đúng thứ tự
    source, spans = _source_index(data)
    starts = {s for s, _, _, _ in spans}
    ends = {t for _, t, _, _ in spans}
    pos = 0
    not_verbatim, out_of_order, mid_cut = [], [], []
    matched = set()
    for ln in lines_of(extract):
        for s in split_sentences(ln):
            s = norm(s)
            idx = source.find(s, pos)
            status = "ok"
            if idx >= 0:
                end = idx + len(s)
                pos = end
                for (a, b, dn, uid) in spans:
                    if a < end and b > idx:
                        matched.add((dn, uid))
                if end not in ends:
                    status = "cut"
                    mid_cut.append(s)
                elif idx not in starts:
                    status = "lead_cut"
            elif source.find(s) >= 0:
                status = "out_of_order"
                out_of_order.append(s)
            else:
                status = "not_verbatim"
                not_verbatim.append(s)
            res.extract_sentences.append({"text": s, "status": status})
    for s in not_verbatim[:5]:
        res.add(
            ERROR,
            "not_verbatim",
            "Bản trích chọn có câu không phải nguyên văn bản gốc: “" + s[:90] + "…”",
            "extract",
        )
    for s in out_of_order[:5]:
        res.add(
            ERROR,
            "out_of_order",
            "Bản trích chọn sai thứ tự so với bản gốc: “" + s[:90] + "…”",
            "extract",
        )
    if len(not_verbatim) > 5 or len(out_of_order) > 5:
        res.add(ERROR, "more", "... còn nhiều câu khác cùng lỗi.", "extract")
    for s in mid_cut[:3]:
        res.add(
            WARNING,
            "mid_cut",
            "Câu bị cắt ở giữa/cuối (hướng dẫn chỉ cho bỏ thành phần phụ ở đầu câu): “"
            + s[:90] + "…”",
            "extract",
        )
    res.matched_units = [{"doc": dn, "id": uid} for dn, uid in sorted(matched)]
    if len(data["docs"]) > 1:
        docs_hit = {dn for dn, _ in matched}
        missing = [d["n"] for d in data["docs"] if d["n"] not in docs_hit]
        if missing:
            res.add(
                WARNING,
                "doc_unrepresented",
                f"Đa văn bản: văn bản {missing} không có câu nào trong bản trích chọn.",
                "extract",
            )

    # Ước lượng ROUGE-2 (ngưỡng >= 33.00%)
    r2_min = float(g.get("rouge2_min", 0.33)) * 100.0
    r2_est = estimate_rouge2(data, extract)
    res.metrics["rouge2_est"] = r2_est
    if r2_est < r2_min:
        res.add(
            ERROR,
            "rouge2_low",
            f"ROUGE-2 ước lượng = {r2_est:.2f}% < ngưỡng {r2_min:.2f}%: bản trích chọn chưa phủ đủ các câu/cụm từ "
            "trọng tâm. Hãy chọn thêm các câu nêu luận đề, sự kiện chính hoặc sát với gợi ý trọng tâm.",
            "extract",
        )
