"""Heuristic scoring estimates (ROUGE-2, BARTScore, G-Eval) for checks."""
from __future__ import annotations

from collections import Counter
import math

from ..textutil import bigrams, count_words
from .core import ERROR, WARNING, CheckResult, _tokens_lower


def estimate_rouge2(data: dict, extract: str) -> float:
    """Ước lượng điểm ROUGE-2 (%) của bản trích chọn (ngưỡng chuẩn >= 33.00%)."""
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
