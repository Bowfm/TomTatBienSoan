"""Kiểm tra cơ học bản trích chọn và bản biên soạn.

Tái cấu trúc từ skill_ref/.../scripts/check.py thành hàm thư viện: CLI, pipeline và giao diện
web đều gọi run_checks() - không có bản thứ hai.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from . import STYLES
from .textutil import bigrams, count_words, lines_of, nfc, norm, split_sentences
from .units import abstract_targets

BULLET_RE = re.compile(r"^\s*([-•*–—+·▪●○■►✓]|\d+[.)/]|[a-zA-Zđ][.)])\s+")
NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
DEC_POINT_RE = re.compile(r"(?<![\d.,])\d+\.\d{1,2}(?!\d|\.\d)")
NO_BULLET_STYLES = ("sinh hoạt hằng ngày", "báo chí - công luận", "chính luận")

ERROR, WARNING, INFO = "error", "warning", "info"
LEVEL_VI = {ERROR: "LỖI", WARNING: "CẢNH BÁO", INFO: "GHI CHÚ"}


@dataclass
class Issue:
    level: str
    code: str
    message: str
    target: str = "abstract"  # extract | abstract | label

    def to_dict(self) -> dict:
        d = asdict(self)
        d["level_vi"] = LEVEL_VI[self.level]
        return d


@dataclass
class CheckResult:
    metrics: dict = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    extract_sentences: list[dict] = field(default_factory=list)
    matched_units: list[dict] = field(default_factory=list)

    def add(self, level, code, message, target="abstract"):
        self.issues.append(Issue(level, code, message, target))

    def of(self, level=None, target=None) -> list[Issue]:
        return [i for i in self.issues
                if (level is None or i.level == level) and (target is None or i.target == target)]

    @property
    def errors(self) -> list[Issue]:
        return self.of(ERROR)

    @property
    def warnings(self) -> list[Issue]:
        return self.of(WARNING)

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "metrics": self.metrics,
            "issues": [i.to_dict() for i in self.issues],
            "n_errors": len(self.errors),
            "n_warnings": len(self.warnings),
            "extract_sentences": self.extract_sentences,
            "matched_units": self.matched_units,
        }

    def text_report(self) -> str:
        return "\n".join(f"[{LEVEL_VI[i.level]}] {i.message}" for i in self.issues)


def is_heading_like(line: str) -> bool:
    s = line.strip()
    return count_words(s) <= 14 and not re.search(r"[.!?…\"”):;]$", s)


def _tokens_lower(text: str | None) -> list[str]:
    return re.findall(r"\w+", nfc(text).lower())


def estimate_rouge2(data: dict, extract: str) -> float:
    """Ước lượng điểm ROUGE-2 (%) của bản trích chọn (ngưỡng chuẩn >= 33.00%)."""
    from collections import Counter

    ext_bi = bigrams(extract)
    if not ext_bi:
        return 0.0
    ext_counts = Counter(ext_bi)
    scores: list[float] = []

    # 1. So với gợi ý trích chọn tham chiếu trong file (ai_extract) nếu có
    ai_ext = (data.get("ai_extract") or "").strip()
    if ai_ext and count_words(ai_ext) >= 15:
        ref_bi = bigrams(ai_ext)
        if ref_bi:
            ref_counts = Counter(ref_bi)
            overlap = sum(min(ext_counts[b], ref_counts[b]) for b in ext_counts)
            recall = overlap / len(ref_bi)
            prec = overlap / len(ext_bi)
            f1 = (2 * prec * recall / (prec + recall)) if (prec + recall) else 0.0
            scores.append(max(recall, f1))

    # 2. So với tập câu trọng tâm (pseudo-reference ~32% số từ có độ quan trọng câu cao nhất của văn bản gốc)
    all_src_bi = []
    for d in data.get("docs") or []:
        for u in d.get("units") or []:
            all_src_bi.extend(bigrams(u["text"]))
    if all_src_bi:
        freq = Counter(all_src_bi)
        q_toks = set(_tokens_lower(data.get("query") or ""))
        scored_units = []
        for d in data.get("docs") or []:
            for idx, u in enumerate(d.get("units") or []):
                u_bi = bigrams(u["text"])
                if not u_bi:
                    continue
                centrality = sum(freq[b] for b in u_bi) / (len(u_bi) ** 0.85)
                if idx < 3:
                    centrality *= 1.25
                if q_toks and (q_toks & set(_tokens_lower(u["text"]))):
                    centrality *= 1.35
                scored_units.append((centrality, u["words"], u_bi))
        scored_units.sort(key=lambda x: x[0], reverse=True)
        target_w = max(40, int(data["budget"]["total_words"] * 0.32))
        salient_bi: list[tuple[str, str]] = []
        acc_w = 0
        for _, w, u_bi in scored_units:
            salient_bi.extend(u_bi)
            acc_w += w
            if acc_w >= target_w:
                break
        if salient_bi:
            sal_counts = Counter(salient_bi)
            overlap_s = sum(min(ext_counts[b], sal_counts[b]) for b in ext_counts)
            recall_s = overlap_s / len(salient_bi)
            prec_s = overlap_s / len(ext_bi)
            f1_s = (2 * prec_s * recall_s / (prec_s + recall_s)) if (prec_s + recall_s) else 0.0
            scores.append(max(recall_s, f1_s))

    raw = max(scores) if scores else 0.0
    return round(min(1.0, raw) * 100.0, 2)


def estimate_bartscore(src_plain: str, extract: str, abstract: str, missing_nums: int = 0) -> float:
    """Ước lượng BARTScore (log-likelihood sinh bản biên soạn từ văn bản gốc; ngưỡng >= -2.56)."""
    import math

    abs_toks = _tokens_lower(abstract)
    if not abs_toks:
        return -5.00
    src_tok_set = set(_tokens_lower(src_plain))
    ext_tok_set = set(_tokens_lower(extract))
    src_bi_set = set(bigrams(src_plain))
    ext_bi_set = set(bigrams(extract))
    abs_bi = bigrams(abstract)

    uni_src = sum(1 for t in abs_toks if t in src_tok_set) / len(abs_toks)
    uni_ext = sum(1 for t in abs_toks if t in ext_tok_set) / len(abs_toks) if ext_tok_set else uni_src
    bi_src = (sum(1 for b in abs_bi if b in src_bi_set) / len(abs_bi)) if abs_bi else uni_src
    bi_ext = (sum(1 for b in abs_bi if b in ext_bi_set) / len(abs_bi)) if (abs_bi and ext_bi_set) else bi_src

    p_gen = 0.35 * uni_src + 0.15 * uni_ext + 0.35 * bi_src + 0.15 * bi_ext
    p_eff = max(0.015, min(0.85, (p_gen ** 1.85) * 0.52))
    score = math.log(p_eff) - (0.18 * missing_nums)
    return round(score, 2)


def estimate_geval_heuristic(res: CheckResult, bart_est: float, rouge2_est: float) -> float:
    """Ước lượng nhanh điểm G-Eval (thang 1.00 - 5.00, ngưỡng >= 4.25) khi chưa có điểm chấm từ LLM."""
    if not res.metrics.get("abstract_words"):
        return 0.0
    score = 4.65
    for iss in res.issues:
        if iss.code in ("rouge2_low", "bartscore_low", "geval_low"):
            continue
        if iss.level == ERROR:
            score -= 0.45
        elif iss.level == WARNING:
            score -= 0.15
    if bart_est < -2.56:
        score -= min(0.8, (-2.56 - bart_est) * 0.6)
    if rouge2_est < 33.0:
        score -= min(0.5, (33.0 - rouge2_est) * 0.02)
    return round(max(1.0, min(5.0, score)), 2)


# ------------------------------------------------------------------ trích chọn

def _source_index(data: dict):
    """Chuỗi nguồn nối các đơn vị + bản đồ vị trí -> đơn vị."""
    parts, spans = [], []
    pos = 0
    for d in data["docs"]:
        for u in d["units"]:
            t = norm(u["text"])
            if parts:
                pos += 1  # khoảng trắng nối
            spans.append((pos, pos + len(t), d["n"], u["id"]))
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
        res.add(ERROR, "compress_high",
                f"Bản trích chọn {n_ext} từ = {compress:.1%} bản gốc ({total} từ), vượt "
                f"{g['compress_max']:.0%}: cần bớt ít nhất {n_ext - hi} từ (nên bớt khoảng "
                f"{n_ext - mid} từ để về ~{e['target']:.0%}).", "extract")
    elif compress < g["compress_min"]:
        res.add(ERROR, "compress_low",
                f"Bản trích chọn {n_ext} từ = {compress:.1%} bản gốc ({total} từ), dưới "
                f"{g['compress_min']:.0%}: cần thêm ít nhất {lo - n_ext} từ (nên thêm khoảng "
                f"{mid - n_ext} từ để về ~{e['target']:.0%}).", "extract")
    elif compress < e["target_low"] or compress > e["target_high"]:
        delta = mid - n_ext
        how = f"thêm khoảng {delta} từ" if delta > 0 else f"bớt khoảng {-delta} từ"
        res.add(WARNING, "compress_edge",
                f"Compress {compress:.3f} đạt nhưng sát biên (nên {e['target_low']:.2f}-"
                f"{e['target_high']:.2f}); {how} để về ~{e['target']:.0%}.", "extract")

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
        res.add(ERROR, "not_verbatim",
                "Bản trích chọn có câu không phải nguyên văn bản gốc: “" + s[:90] + "…”", "extract")
    for s in out_of_order[:5]:
        res.add(ERROR, "out_of_order",
                "Bản trích chọn sai thứ tự so với bản gốc: “" + s[:90] + "…”", "extract")
    if len(not_verbatim) > 5 or len(out_of_order) > 5:
        res.add(ERROR, "more", "... còn nhiều câu khác cùng lỗi.", "extract")
    for s in mid_cut[:3]:
        res.add(WARNING, "mid_cut",
                "Câu bị cắt ở giữa/cuối (hướng dẫn chỉ cho bỏ thành phần phụ ở đầu câu): “"
                + s[:90] + "…”", "extract")
    res.matched_units = [{"doc": dn, "id": uid} for dn, uid in sorted(matched)]
    if len(data["docs"]) > 1:
        docs_hit = {dn for dn, _ in matched}
        missing = [d["n"] for d in data["docs"] if d["n"] not in docs_hit]
        if missing:
            res.add(WARNING, "doc_unrepresented",
                    f"Đa văn bản: văn bản {missing} không có câu nào trong bản trích chọn.", "extract")

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


# ------------------------------------------------------------------ biên soạn

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


def run_checks(data: dict, style: str | None, extract: str | None, abstract: str | None,
               cfg: dict) -> CheckResult:
    """Kiểm tra toàn bộ. style/extract/abstract có thể rỗng (kiểm tra từng phần)."""
    res = CheckResult()
    style = nfc(style or "").strip()
    extract = nfc(extract or "")
    abstract = nfc(abstract or "")
    if not style:
        res.add(ERROR, "label_missing", "Chưa có nhãn phong cách chức năng.", "label")
    elif style not in STYLES:
        res.add(ERROR, "label_invalid", f"Nhãn '{style}' không thuộc 5 giá trị hợp lệ.", "label")
    check_extract(data, extract, cfg, res)
    check_abstract(data, style, extract, abstract, cfg, res)
    return res


def retry_feedback(res: CheckResult, cfg: dict, target: str = "abstract") -> list[str]:
    """Danh sách vấn đề cụ thể gửi lại cho model ở lần thử sau."""
    codes = set(cfg["abstract"].get("retry_on_warnings", []))
    return [i.message for i in res.issues
            if i.target == target and (i.level == ERROR or (i.level == WARNING and i.code in codes))]


def needs_retry(res: CheckResult, cfg: dict, target: str = "abstract") -> bool:
    return bool(retry_feedback(res, cfg, target))


def quality_key(res: CheckResult, n_target: float | None = None) -> tuple:
    """Khóa sắp xếp để chọn bản tốt nhất (nhỏ hơn = tốt hơn)."""
    n_abs = res.metrics.get("abstract_words", 0)
    dist = abs(n_abs - n_target) if n_target else 0
    return (len(res.errors), len(res.warnings), dist)
