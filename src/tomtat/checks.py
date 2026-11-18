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

# Từ khóa nhận diện nội dung chuyển tiền / biên lai giao dịch ngân hàng
BANK_KEYWORDS = {
    "chuyển khoản", "chuyển tiền", "số tài khoản", "stk", "ngân hàng", "mã giao dịch",
    "giao dịch thành công", "thụ hưởng", "người thụ hưởng", "tài khoản nguồn", "tài khoản nhận",
    "biên lai", "lệnh chuyển", "biến động số dư", "vietcombank", "techcombank", "vietinbank",
    "bidv", "mbbank", "acb", "tpbank", "vpbank", "agribank", "sacombank", "vnpay", "momo",
    "nội dung chuyển", "nội dung giao dịch", "số dư khả dụng", "ngày hạch toán"
}

VI_STOPWORDS = {
    "và", "của", "các", "có", "cho", "là", "trong", "để", "với", "những", "người", "được",
    "khi", "đã", "sẽ", "ở", "này", "đó", "ra", "vào", "về", "thì", "mà", "như", "theo",
    "tại", "một", "lại", "từ", "phải", "lên", "bị", "hay", "cũng", "đến", "trên", "rất",
    "nhiều", "nhưng", "bởi", "vì", "do", "tự", "nào", "cả", "nữa", "qua", "sau", "trước"
}


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


def check_suspicious_document(data: dict) -> list[str]:
    """Phát hiện các file có dấu hiệu lỗi bất thường có thể bị hủy:
    1. File chỉ có nội dung chuyển tiền / biên lai giao dịch ngân hàng thực sự (STK, VND, biến động số dư thô)
    2. File không có nội dung chính: danh sách đường link (URL dump), từ điển/bộ thủ, danh bạ/địa chỉ, bảng số liệu thô
    3. File lặp từ bất thường hoặc lặp câu liên tiếp
    4. File cách đoạn không có nghĩa (bị phân mảnh vụn vặt hoặc ký tự rác)
    5. File quá ngắn (< 35 từ) hoặc bảng Word bị lỗi cấu trúc
    """
    reasons: list[str] = []
    docs = data.get("docs") or []
    if not docs:
        return ["File không có nội dung văn bản nào."]

    all_units = [u for d in docs for u in d.get("units") or []]
    all_text = nfc(" ".join(u.get("text", "") for u in all_units)).strip()
    total_w = sum(u.get("words", 0) for u in all_units) or count_words(all_text)
    text_lower = all_text.lower()

    # 1. File quá ngắn / không có nội dung chính
    if total_w < 35:
        reasons.append(f"File quá ngắn (chỉ có {total_w} từ < 35 từ), không đủ nội dung chính để tóm tắt và biên soạn.")

    # 2. File chỉ có nội dung chuyển tiền / biên lai ngân hàng thực sự (tránh nhầm lẫn với bài luận/chia sẻ về nghề trading, tài chính)
    if total_w <= 350:
        has_transfer_term = any(t in text_lower for t in [
            "chuyển khoản", "chuyển tiền", "lệnh chuyển", "ủy nhiệm chi",
            "biên lai chuyển", "biến động số dư", "chuyển thành công"
        ])
        has_account_pattern = bool(re.search(r"(?:stk|số tài khoản|tài khoản nguồn|tài khoản nhận|tài khoản thụ hưởng|tk nhận)\s*[:.]?\s*[\d\w]{6,}", text_lower))
        has_money_pattern = bool(re.search(r"(?:số tiền|biến động|số dư|giao dịch)\s*[:.]?\s*[-+]?\d+[\d.,]*\s*(?:vnd|vnđ|đ|đồng)", text_lower))
        has_ref_code = bool(re.search(r"(?:mã giao dịch|mã gd|số tham chiếu|ft\d{8,}|\bref\b)\s*[:.]?\s*[\w\d]+", text_lower))
        ev_cnt = sum([has_transfer_term, has_account_pattern, has_money_pattern, has_ref_code])
        if (ev_cnt >= 2 and total_w <= 200) or ev_cnt >= 3:
            reasons.append("File chỉ là tin nhắn/biên lai chuyển tiền ngân hàng (chứa STK, số tiền, mã giao dịch thô).")

    # 3. File không có nội dung bài viết: Danh sách link / URL dump (như đề thi dán link Facebook hàng loạt)
    urls = re.findall(r"https?://[^\s]+", all_text)
    if len(urls) >= 5 or (urls and sum(len(u) for u in urls) / max(1, len(all_text)) > 0.15):
        reasons.append(f"File chỉ là danh sách đường link/URL liên kết ({len(urls)} link), không có bài viết chính.")

    # 4. File không có nội dung bài viết: Danh bạ địa chỉ / quán xá liệt kê bằng dấu chấm phẩy dày đặc
    semis = all_text.count(";")
    if semis >= 25 or (semis >= 15 and semis / max(1, total_w / 20) > 1.2):
        reasons.append(f"File chỉ là danh bạ/danh sách địa chỉ quán xá liệt kê thô ({semis} dấu chấm phẩy).")

    # 5. File không có nội dung bài viết: Từ điển tra cứu bộ thủ / ghép từ vựng lặp cấu trúc
    radicals = len(re.findall(r"\bbộ\s+[A-ZĐÀ-Ỹa-zđà-ỹ]+(?:\s*\([^)]*\))?\s*[:–-]", all_text))
    vocab_rhyme = len(re.findall(r"\b(?:có nghĩa là|tạm dịch|nghĩa gốc là|dùng để chỉ|tên gọi|chữ|là con|là cái|là người|thì là|gọi là|dịch là|nghĩa là)\b", text_lower))
    if radicals >= 10:
        reasons.append(f"File là danh mục từ điển/bộ thủ chữ Hán tra cứu ({radicals} mục bộ thủ), không có bài viết chính.")
    elif vocab_rhyme >= 25 and total_w >= 100:
        reasons.append(f"File là danh sách từ vựng/bài ghép từ tra cứu cơ học ({vocab_rhyme} cụm giải nghĩa từ), không có bài viết chính.")

    # 6. File không có nội dung bài viết: Bảng số liệu / lịch trình thô số thứ tự 63 tỉnh thành
    provs = len(re.findall(r"\b\d{1,2}\s+(?:An Giang|Bà Rịa|Bạc Liêu|Bắc Giang|Bắc Kạn|Bắc Ninh|Bến Tre|Bình Dương|Bình Định|Bình Phước|Bình Thuận|Cà Mau|Cần Thơ|Cao Bằng|Đà Nẵng|Đắk Lắk|Đắk Nông|Điện Biên|Đồng Nai|Đồng Tháp|Gia Lai|Hà Giang|Hà Nam|Hà Nội|Hà Tĩnh|Hải Dương|Hải Phòng|Hậu Giang|Hòa Bình|Hưng Yên|Khánh Hòa|Kiên Giang|Kon Tum|Lai Châu|Lâm Đồng|Lạng Sơn|Lào Cai|Long An|Nam Định|Nghệ An|Ninh Bình|Ninh Thuận|Phú Thọ|Phú Yên|Quảng Bình|Quảng Nam|Quảng Ngãi|Quảng Ninh|Quảng Trị|Sóc Trăng|Sơn La|Tây Ninh|Thái Bình|Thái Nguyên|Thanh Hóa|Thừa Thiên Huế|Tiền Giang|TP\.?HCM|Trà Vinh|Tuyên Quang|Vĩnh Long|Vĩnh Phúc|Yên Bái)\b", all_text, re.IGNORECASE))
    if provs >= 15:
        reasons.append(f"File là bảng dữ liệu/lịch trình thô liệt kê theo số thứ tự tỉnh thành ({provs} tỉnh thành), không có bài viết chính.")

    # 7. File lặp từ bất thường
    tokens = re.findall(r"\b\w+\b", text_lower)
    if len(tokens) >= 30:
        from collections import Counter
        content_tokens = [t for t in tokens if len(t) >= 2 and t not in VI_STOPWORDS and not t.isdigit()]
        if content_tokens:
            counts = Counter(content_tokens)
            top_word, top_cnt = counts.most_common(1)[0]
            pct_content = top_cnt / len(content_tokens)
            pct_total = top_cnt / len(tokens)
            if (pct_content > 0.18 and top_cnt >= 8) or (pct_total > 0.14 and top_cnt >= 10):
                reasons.append(f"File lặp từ bất thường: từ “{top_word}” lặp lại {top_cnt} lần (chiếm {pct_total:.1%} văn bản).")

        if len(tokens) >= 60:
            vocab_div = len(set(tokens)) / len(tokens)
            if vocab_div < 0.20:
                reasons.append(f"File có độ đa dạng từ vựng quá thấp ({vocab_div:.1%}), nghi lỗi lặp từ/lặp đoạn.")

    # Lặp lại các câu/unit nguyên văn liên tiếp
    for d in docs:
        units = d.get("units") or []
        consec = 0
        for i in range(1, len(units)):
            u_prev = norm(units[i - 1].get("text", ""))
            u_curr = norm(units[i].get("text", ""))
            if len(u_curr) > 12 and u_curr == u_prev:
                consec += 1
                if consec >= 2:  # 3 câu giống nhau liên tiếp
                    reasons.append("File có các đoạn/câu lặp lại nguyên văn liên tiếp.")
                    break
            else:
                consec = 0

    # 8. File cách đoạn không có nghĩa / phân mảnh vụn vặt / ký tự lạ
    if len(all_units) >= 8:
        avg_w = sum(u.get("words", 0) for u in all_units) / len(all_units)
        if avg_w < 2.5:
            reasons.append(f"File cách đoạn không có nghĩa: văn bản bị cắt vụn quá mức (trung bình {avg_w:.1f} từ/đoạn).")

    if len(all_text) >= 40:
        std_chars = len(re.findall(r"[\w\s.,;:!?\"'()–—\-/]", all_text))
        noise_ratio = (len(all_text) - std_chars) / len(all_text)
        if noise_ratio > 0.15:
            reasons.append(f"File chứa quá nhiều ký tự lạ/ký hiệu bất thường ({noise_ratio:.1%}).")

    return reasons

    return reasons

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


def run_checks(data: dict, style: str | None, extract: str | None, abstract: str | None,
               cfg: dict) -> CheckResult:
    """Kiểm tra toàn bộ. style/extract/abstract có thể rỗng (kiểm tra từng phần)."""
    res = CheckResult()
    style = nfc(style or "").strip()
    extract = nfc(extract or "")
    abstract = nfc(abstract or "")

    # Kiểm tra file bất thường có thể bị hủy
    suspicious_reasons = check_suspicious_document(data)
    if suspicious_reasons:
        res.metrics["is_suspicious"] = True
        res.metrics["suspicious_reasons"] = suspicious_reasons
        for reason in suspicious_reasons:
            res.add(WARNING, "suspicious_file", f"[CẢNH BÁO HỦY BÀI] {reason}", "extract")
    else:
        res.metrics["is_suspicious"] = False
        res.metrics["suspicious_reasons"] = []

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
