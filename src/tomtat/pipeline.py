"""Pipeline xử lý từng file .docx: inspect -> label -> extract -> abstract -> judge."""
from __future__ import annotations

import copy
from pathlib import Path
import threading

from . import STYLES
from .checks import estimate_rouge2, needs_retry, quality_key, retry_feedback, run_checks
from .docx_io import DocxError
from .llm import (
    BaseClient,
    LLMCancelledError,
    LLMError,
    bind_cancel_event,
    check_not_cancelled,
    get_active_cancel_event,
)
from .memory import format_label_memory, format_style_memory
from .models import AbstractStep, ExtractStep, FileState, JudgeStep, LabelStep, UnitsData
from .prompts import ABSTRACT_SCHEMA, EXTRACT_SCHEMA, JUDGE_SCHEMA, LABEL_SCHEMA, PromptRenderer
from .state import (
    DONE_STATUSES,
    STATUS_APPROVED,
    STATUS_DRAFT,
    STATUS_ERROR,
    STATUS_EXPORTED,
    STATUS_MANUAL,
    STATUS_PARTIAL,
    STATUS_PENDING,
    FileWorkspace,
)
from .textutil import configure as configure_textutil, count_words, nfc
from .units import (
    abstract_targets,
    assemble_extract,
    inspect_docx,
    normalize_selections,
    source_text,
    units_listing,
)

STEPS_ORDER = ("inspect", "label", "extract", "abstract", "judge")
LABEL_REVIEW_FLAG = "Cần xem kỹ nhãn phong cách (mức chắc chắn thấp/trung bình hoặc lệch đa số)."

__all__ = [
    "STEPS_ORDER",
    "LABEL_REVIEW_FLAG",
    "Pipeline",
    "process_file",
    "step_inspect",
    "step_label",
    "step_extract",
    "step_abstract",
    "step_judge",
    "finalize_status",
    "sync_manual_label_metadata",
]


def _add_flag(st: dict, flag: str) -> None:
    flags = st.setdefault("flags", [])
    if flag not in flags:
        flags.append(flag)


def _remove_flag(st: dict, flag: str) -> None:
    flags = st.get("flags") or []
    if flag in flags:
        flags.remove(flag)


def _step_has_content(st: dict, step: str) -> bool:
    if step == "extract":
        ext = st.get("extract") or {}
        return bool(nfc(str(ext.get("text") or "")).strip() or ext.get("selections"))
    if step == "abstract":
        ab = st.get("abstract") or {}
        return bool(nfc(str(ab.get("text") or "")).strip())
    if step == "judge":
        return bool(st.get("judge"))
    return False


def _mark_stale_after(st: dict, step: str, *, reason: str | None = None) -> list[str]:
    if step not in STEPS_ORDER:
        return []
    idx = STEPS_ORDER.index(step)
    stale = set(st.get("stale_steps") or [])
    newly_marked: list[str] = []
    for s in STEPS_ORDER[idx + 1:]:
        if _step_has_content(st, s):
            stale.add(s)
            newly_marked.append(s)
    stale.discard(step)
    ordered = [s for s in STEPS_ORDER if s in stale]
    st["stale_steps"] = ordered
    if not ordered:
        st["stale_reason"] = None
    elif newly_marked and reason:
        st["stale_reason"] = reason
    return newly_marked


def _clear_stale(st: dict, step: str) -> None:
    stale = [s for s in (st.get("stale_steps") or []) if s != step]
    st["stale_steps"] = stale
    if not stale:
        st["stale_reason"] = None


def _ai_label_baseline(lb: dict) -> str:
    """Trả về nhãn gốc do AI gán (nếu có) để phân biệt chỉnh sửa thủ công và giữ nguyên/hoàn tác."""
    for candidate in (
        lb.get("ai_value"),
        lb.get("computed_majority") if lb.get("computed_majority") in STYLES else "",
        lb.get("model_label") if lb.get("model_label") in STYLES else "",
    ):
        val = nfc(str(candidate or "")).strip()
        if val:
            return val
    return ""


def _snapshot_ai_label_fields(lb: dict, fallback_value: str) -> None:
    """Lưu lại thông tin nhãn AI ban đầu một lần trước khi ghi đè bằng lựa chọn thủ công."""
    if "ai_value" in lb:
        return
    lb["ai_value"] = fallback_value
    lb["ai_reason"] = lb.get("reason", "")
    lb["ai_confidence"] = lb.get("confidence", "")
    lb["ai_per_doc"] = copy.deepcopy(lb.get("per_doc") or [])
    lb["ai_model_used"] = lb.get("model_used")


def sync_manual_label_metadata(st: dict, new_style: str, cfg: dict | None = None) -> bool:
    """Đồng bộ phần giải thích/độ chắc chắn khi người dùng chỉnh sửa phong cách thủ công.

    - Nếu đổi sang phong cách khác với gợi ý ban đầu của AI (hoặc tự chọn khi chưa có AI):
      lưu giữ dữ liệu AI ban đầu vào các trường `ai_*` (phục vụ tự học & hoàn tác),
      cập nhật `reason` rõ ràng theo phong cách mới, đặt `confidence="thu_cong"`,
      `manual_edited=True`, xóa `per_doc`/`model_used` cũ và gỡ cờ cảnh báo xem lại nhãn AI.
    - Nếu chọn lại đúng nhãn AI ban đầu: khôi phục giải thích/độ chắc chắn/cờ AI gốc.
    - Nếu không thay đổi so với trạng thái hiện tại: giữ nguyên (no-op).
    """
    lb = st.setdefault("label", {})
    prev_val = nfc(str(lb.get("value") or "")).strip()
    new_val = nfc(str(new_style or "")).strip()
    ai_val = _ai_label_baseline(lb)

    # State hoặc test cũ chỉ lưu value + reason/confidence mà chưa có model_label/computed_majority
    if (
        not lb.get("manual_edited")
        and not ai_val
        and prev_val
        and new_val != prev_val
        and (lb.get("reason") or lb.get("confidence") or lb.get("model_used") or lb.get("per_doc"))
    ):
        ai_val = prev_val

    has_legacy_mismatch = bool(
        not lb.get("manual_edited")
        and ai_val
        and new_val
        and new_val != ai_val
    )
    style_changed = new_val != prev_val

    if not style_changed and not has_legacy_mismatch:
        return False

    if not new_val:
        if not lb.get("manual_edited") and ai_val:
            _snapshot_ai_label_fields(lb, ai_val)
        lb["value"] = ""
        lb["manual_edited"] = False
        lb["confidence"] = ""
        lb["reason"] = ""
        lb["per_doc"] = []
        lb["model_used"] = None
        _remove_flag(st, LABEL_REVIEW_FLAG)
        return True

    if ai_val and new_val == ai_val:
        if lb.get("manual_edited") or "ai_reason" in lb or "ai_confidence" in lb:
            restored_conf = lb.pop("ai_confidence", lb.get("confidence", ""))
            lb["value"] = new_val
            lb["manual_edited"] = False
            lb["reason"] = lb.pop("ai_reason", "")
            lb["confidence"] = restored_conf
            lb["per_doc"] = lb.pop("ai_per_doc", [])
            lb["model_used"] = lb.pop("ai_model_used", None)
            lb.pop("ai_value", None)

            ok_conf = set(((cfg or {}).get("label") or {}).get("ok_confidence", ["cao"]))
            raw_lb = nfc(str(lb.get("model_label") or "")).strip()
            maj_lb = nfc(str(lb.get("computed_majority") or "")).strip()
            _remove_flag(st, LABEL_REVIEW_FLAG)
            if (restored_conf and restored_conf not in ok_conf) or (
                raw_lb in STYLES and maj_lb in STYLES and raw_lb != maj_lb
            ):
                _add_flag(st, LABEL_REVIEW_FLAG)
        else:
            lb["value"] = new_val
        return True

    if not lb.get("manual_edited") and (
        ai_val or lb.get("reason") or lb.get("confidence") or lb.get("model_used") or lb.get("per_doc")
    ):
        _snapshot_ai_label_fields(lb, ai_val or prev_val)

    ai_hint = nfc(str(lb.get("ai_value") or ai_val or "")).strip()
    lb["value"] = new_val
    lb["manual_edited"] = True
    lb["confidence"] = "thu_cong"
    lb["per_doc"] = []
    lb["model_used"] = None
    if ai_hint and ai_hint != new_val:
        lb["reason"] = (
            f"Người duyệt đã chỉnh sửa thủ công sang phong cách '{new_val}' "
            f"(trước đó AI gợi ý '{ai_hint}')."
        )
    else:
        lb["reason"] = f"Người duyệt đã chọn thủ công phong cách '{new_val}'."
    _remove_flag(st, LABEL_REVIEW_FLAG)
    return True


# ------------------------------------------------------------------ Bước 0: inspect

def step_inspect(ws: FileWorkspace, cfg: dict, st: FileState) -> UnitsData:
    sc = cfg.get("sentence_split", {})
    configure_textutil(sc.get("abbreviations"), sc.get("heading_words"))
    units_data = inspect_docx(ws.docx_path, cfg)
    ws.save_units(units_data)
    _clear_stale(st, "inspect")
    st["error_message"] = None

    # Kiểm tra quy ước đặt tên M/S
    if units_data.get("filename_mismatch"):
        _add_flag(st, units_data["filename_mismatch"])
        if cfg.get("layout", {}).get("on_mismatch", "stop") == "stop":
            st["status"] = STATUS_PARTIAL
    # Kiểm tra 3 hàng nhãn đã có nội dung
    if units_data["layout"].get("label_rows_with_content"):
        _add_flag(
            st,
            f"3 hàng nhãn đã có sẵn nội dung ở hàng {[i + 1 for i in units_data['layout']['label_rows_with_content']]}.",
        )
    for note in units_data["layout"].get("notes") or []:
        _add_flag(st, note)
    ws.save_state(st)
    return units_data


# ------------------------------------------------------------------ Bước 1: label

def step_label(ws: FileWorkspace, units_data: UnitsData, client: BaseClient,
               renderer: PromptRenderer, cfg: dict, st: FileState) -> None:
    prompt = renderer.render(
        "label.md",
        docs=units_data["docs"],
        source_text=source_text(units_data),
        learned_label_memory=format_label_memory(ws.dir.parent),
    )
    res = client.generate_json(
        "label",
        renderer.system(),
        prompt,
        LABEL_SCHEMA,
        log_dir=ws.logs_dir,
        attempt=1,
        context={"data": units_data},
    )
    out = res.data
    raw_label = nfc(out.get("label", "")).strip()
    per_doc_raw = out.get("per_doc") or []

    # Cộng số từ theo nhãn từng văn bản để tính nhãn chiếm đa số
    doc_words = {d["n"]: d["words"] for d in units_data["docs"]}
    style_words: dict[str, int] = {}
    per_doc_clean = []
    for item in per_doc_raw:
        dn = int(item.get("doc", 1))
        lb = nfc(item.get("label", "")).strip()
        w = doc_words.get(dn, 0)
        if lb in STYLES:
            style_words[lb] = style_words.get(lb, 0) + w
        per_doc_clean.append({
            "doc": dn,
            "label": lb,
            "words": w,
            "reason": nfc(item.get("reason", "")).strip(),
        })
    majority = max(style_words, key=style_words.get) if style_words else raw_label
    final_label = majority if majority in STYLES else (raw_label if raw_label in STYLES else STYLES[0])

    conf = nfc(out.get("confidence", "trung_binh")).strip()
    ok_conf = set(cfg.get("label", {}).get("ok_confidence", ["cao"]))
    _remove_flag(st, LABEL_REVIEW_FLAG)
    if conf not in ok_conf or (raw_label in STYLES and majority in STYLES and raw_label != majority):
        _add_flag(st, LABEL_REVIEW_FLAG)

    st["label"] = {
        "value": final_label,
        "model_label": raw_label,
        "computed_majority": majority,
        "per_doc": per_doc_clean,
        "confidence": conf,
        "reason": nfc(out.get("reason", "")).strip(),
        "model_used": res.model,
        "manual_edited": False,
    }
    st["model_used"] = res.model
    if st.get("status") not in DONE_STATUSES:
        st["status"] = STATUS_PENDING
    _clear_stale(st, "label")
    ws.save_state(st)


# ------------------------------------------------------------------ Bước 2: extract

def step_extract(ws: FileWorkspace, units_data: UnitsData, client: BaseClient,
                 renderer: PromptRenderer, cfg: dict, st: FileState) -> None:
    ext_cfg = cfg["extract"]
    g_cfg = cfg["grading"]
    max_attempts = int(ext_cfg.get("max_attempts", 3))
    from .units import compute_budget
    total = units_data["budget"]["total_words"]
    budget = compute_budget(total, cfg)
    units_data["budget"] = budget

    retry_msg = ""
    best = None
    best_dist = float("inf")

    for attempt in range(1, max_attempts + 1):
        check_not_cancelled()
        prompt = renderer.render(
            "extract.md",
            docs=units_data["docs"],
            budget=budget,
            query=units_data.get("query", ""),
            ai_extract=units_data.get("ai_extract", ""),
            units_listing=units_listing(units_data, with_words=True),
            retry_message=retry_msg,
        )
        res = client.generate_json(
            "extract",
            renderer.system(),
            prompt,
            EXTRACT_SCHEMA,
            log_dir=ws.logs_dir,
            attempt=attempt,
            context={"data": units_data, "target_ratio": ext_cfg["target"]},
        )
        sel_map, id_errors = normalize_selections(units_data, res.data.get("selections") or [])
        text, per_doc_words = assemble_extract(units_data, sel_map)
        n_ext = count_words(text)
        ratio = n_ext / total if total else 0.0
        r2_est = estimate_rouge2(units_data, text)
        r2_min = float(g_cfg.get("rouge2_min", 0.33)) * 100.0
        note = nfc(res.data.get("note", "")).strip()

        candidate = {
            "selections": {str(k): v for k, v in sel_map.items()},
            "per_doc_words": {str(k): v for k, v in per_doc_words.items()},
            "text": text,
            "words": n_ext,
            "compress": round(ratio, 4),
            "rouge2_est": r2_est,
            "note": note,
            "attempts": attempt,
            "custom_edited": False,
            "model_used": res.model,
        }
        in_hard = (
            g_cfg["compress_min"] <= ratio <= g_cfg["compress_max"]
            and r2_est >= r2_min
            and not id_errors
        )
        dist = abs(ratio - ext_cfg["target"]) + (0 if in_hard else 10) - (r2_est / 1000.0)
        if dist < best_dist:
            best = candidate
            best_dist = dist

        # Kiểm tra xem có cần thử lại không
        problems = list(id_errors)
        if r2_est < r2_min:
            problems.append(
                f"Điểm ROUGE-2 ước lượng hiện chỉ đạt {r2_est:.2f}% (< ngưỡng {r2_min:.2f}%). "
                "Hãy ưu tiên chọn các câu chứa luận đề, sự kiện cốt lõi và các câu sát với phần Gợi ý trích chọn tham chiếu."
            )
        if ratio < g_cfg["compress_min"]:
            need = budget["extract_target_mid"] - n_ext
            problems.append(
                f"Bản trích chọn hiện có {n_ext}/{total} từ ({ratio:.1%}), thấp hơn mức tối thiểu "
                f"{g_cfg['compress_min']:.0%} ({budget['extract_min']} từ). Hãy CHỌN THÊM các câu quan trọng "
                f"khoảng {max(need, budget['extract_min'] - n_ext)} từ nữa để đạt khoảng {budget['extract_target_mid']} từ."
            )
        elif ratio > g_cfg["compress_max"]:
            cut = n_ext - budget["extract_target_mid"]
            problems.append(
                f"Bản trích chọn hiện có {n_ext}/{total} từ ({ratio:.1%}), vượt quá mức tối đa "
                f"{g_cfg['compress_max']:.0%} ({budget['extract_max']} từ). Hãy BỚT các câu phụ khoảng "
                f"{max(cut, n_ext - budget['extract_max'])} từ để đưa về khoảng {budget['extract_target_mid']} từ."
            )
        elif ratio < ext_cfg["target_low"] or ratio > ext_cfg["target_high"]:
            delta = budget["extract_target_mid"] - n_ext
            action = f"chọn thêm khoảng {delta} từ" if delta > 0 else f"bớt khoảng {-delta} từ"
            problems.append(
                f"Tỉ lệ trích chọn hiện là {n_ext}/{total} từ ({ratio:.1%}), nằm ngoài khoảng an toàn "
                f"{ext_cfg['target_low']:.0%}-{ext_cfg['target_high']:.0%} ({budget['extract_target_low']}-{budget['extract_target_high']} từ). Hãy {action}."
            )
        if len(units_data["docs"]) > 1:
            missing_docs = [d["n"] for d in units_data["docs"] if not sel_map.get(d["n"])]
            if missing_docs:
                problems.append(f"Chưa chọn câu nào cho văn bản số {missing_docs}; mỗi văn bản phải có đại diện.")

        if not problems:
            break
        retry_msg = "\n".join(f"- {p}" for p in problems)

    st["extract"] = best or candidate
    st["model_used"] = (st["extract"] or {}).get("model_used") or st.get("model_used", "")
    if st.get("status") not in DONE_STATUSES:
        st["status"] = STATUS_PENDING
    _clear_stale(st, "extract")
    ws.save_state(st)


# ------------------------------------------------------------------ Bước 3: abstract

def _generate_abstract_loop(ws: FileWorkspace, units_data: dict, client: BaseClient,
                            renderer: PromptRenderer, cfg: dict, st: dict,
                            extra_errors: list[str] | None = None,
                            max_attempts: int | None = None) -> None:
    abs_cfg = cfg["abstract"]
    style = st["label"]["value"] or STYLES[0]
    extract_text = st["extract"]["text"]
    n_ext = count_words(extract_text)
    targets = abstract_targets(n_ext, cfg)
    target_mid = (
        (targets["short_low"] + targets["short_high"]) / 2
        if targets["direction"] == "shorter"
        else targets["long_target"]
    )
    limit_attempts = max_attempts or int(abs_cfg.get("max_attempts", 3))
    prev_abstract = st.get("abstract", {}).get("text", "") if extra_errors else ""
    retry_errs = list(extra_errors or [])

    best_candidate = None
    best_check = None
    best_key = (999, 999, 999999.0)

    for attempt in range(1, limit_attempts + 1):
        check_not_cancelled()
        prompt = renderer.render(
            "abstract.md",
            style=style,
            docs=units_data["docs"],
            extract=extract_text,
            extract_words=n_ext,
            targets=targets,
            ratio_limit=abs_cfg["ratio_limit"],
            style_guide=renderer.style_guide(style),
            retry_errors=retry_errs,
            previous_abstract=prev_abstract,
            learned_style_memory=format_style_memory(ws.dir.parent, style),
            query=units_data.get("query", ""),
            ai_abstract=units_data.get("ai_abstract", ""),
            source_text=source_text(units_data),
        )
        res = client.generate_json(
            "abstract",
            renderer.system(),
            prompt,
            ABSTRACT_SCHEMA,
            log_dir=ws.logs_dir,
            attempt=attempt,
            context={"data": units_data, "extract": extract_text, "targets": targets, "style": style},
        )
        from .checks import clean_abstract_text

        n_docs = len(units_data.get("docs", []))
        src_plain = nfc(" ".join(u["text"] for d in units_data.get("docs", []) for u in d.get("units", [])))
        abs_text = clean_abstract_text(
            res.data.get("abstract", ""),
            n_docs=n_docs,
            preserve_source=src_plain,
        )
        note = nfc(res.data.get("note", "")).strip()
        chk = run_checks(units_data, style, extract_text, abs_text, cfg)
        key = quality_key(chk, target_mid)
        if key < best_key:
            best_key = key
            best_check = chk
            best_candidate = {
                "text": abs_text,
                "words": count_words(abs_text),
                "note": note,
                "attempts": st.get("abstract", {}).get("attempts", 0) + attempt,
                "model_used": res.model,
            }
        if not needs_retry(chk, cfg, target="abstract"):
            break
        retry_errs = retry_feedback(chk, cfg, target="abstract")
        prev_abstract = abs_text

    if best_candidate is not None:
        st["abstract"] = best_candidate
        st["checks"] = best_check.to_dict()
        st["model_used"] = best_candidate.get("model_used") or st.get("model_used", "")
    _clear_stale(st, "abstract")
    ws.save_state(st)


def step_abstract(ws: FileWorkspace, units_data: UnitsData, client: BaseClient,
                  renderer: PromptRenderer, cfg: dict, st: FileState) -> None:
    st.setdefault("abstract", {})["attempts"] = 0
    _generate_abstract_loop(ws, units_data, client, renderer, cfg, st)


# ------------------------------------------------------------------ Bước 4: judge (tùy chọn)

def step_judge(ws: FileWorkspace, units_data: UnitsData, client: BaseClient,
               renderer: PromptRenderer, cfg: dict, st: FileState,
               allow_rewrite: bool = True) -> None:
    check_not_cancelled()
    j_cfg = cfg.get("judge", {})
    style = st["label"]["value"] or STYLES[0]
    extract_text = st["extract"]["text"]
    abstract_text = st["abstract"]["text"]

    prompt = renderer.render(
        "judge.md",
        style=style,
        style_guide=renderer.style_guide(style),
        docs=units_data.get("docs") or [],
        query=(units_data.get("query") or "").strip(),
        extract=extract_text,
        abstract=abstract_text,
        abstract_words=count_words(abstract_text),
        source_text=source_text(units_data),
    )
    res = client.generate_json(
        "judge",
        renderer.system(),
        prompt,
        JUDGE_SCHEMA,
        log_dir=ws.logs_dir,
        attempt=1,
        context={"data": units_data},
    )
    d = res.data
    scores = [
        round(max(1.0, min(5.0, float(d.get(k, 1.0)))), 2)
        for k in ("relevance", "coherence", "consistency", "fluency")
    ]
    avg = round(sum(scores) / 4.0, 2)
    issues = [nfc(x).strip() for x in (d.get("issues") or []) if str(x).strip()]
    st["judge"] = {
        "relevance": scores[0],
        "coherence": scores[1],
        "consistency": scores[2],
        "fluency": scores[3],
        "average": avg,
        "issues": issues,
        "summary": nfc(d.get("summary", "")).strip(),
        "rewritten": False,
        "model_used": res.model,
    }
    min_avg = float(j_cfg.get("min_average", 4.25))
    if allow_rewrite and j_cfg.get("rewrite_once_if_below", True) and avg < min_avg:
        fb = issues or [f"Điểm G-Eval trung bình {avg} < {min_avg}: {st['judge']['summary']}"]
        # Giữ bản cũ nếu lần viết lại bị lỗi cơ học nặng hơn
        old_abs = dict(st["abstract"])
        old_chk = st["checks"]
        _generate_abstract_loop(
            ws, units_data, client, renderer, cfg, st,
            extra_errors=[f"[G-Eval góp ý] {x}" for x in fb],
            max_attempts=1,
        )
        new_chk = st["checks"]
        if new_chk["n_errors"] > (old_chk or {}).get("n_errors", 0):
            st["abstract"] = old_abs
            st["checks"] = old_chk
        else:
            st["judge"]["rewritten"] = True
            step_judge(ws, units_data, client, renderer, cfg, st, allow_rewrite=False)
            st["judge"]["rewritten"] = True
            return

    st["flags"] = [
        f for f in st.get("flags", [])
        if not str(f).startswith("Điểm G-Eval tự chấm (")
        and not str(f).startswith("Bỏ qua bước tự chấm G-Eval")
    ]
    if st["judge"]["average"] < min_avg:
        _add_flag(st, f"Điểm G-Eval tự chấm ({st['judge']['average']}) thấp hơn ngưỡng {min_avg}.")
    _clear_stale(st, "judge")
    ws.save_state(st)


# ------------------------------------------------------------------ Điều phối

def finalize_status(units_data: UnitsData, st: FileState, cfg: dict,
                    ignore_mismatch: bool = False) -> FileState:
    """Chạy lại bộ kiểm tra và cập nhật trạng thái cuối của file."""
    chk = run_checks(
        units_data,
        st.get("label", {}).get("value", ""),
        st.get("extract", {}).get("text", ""),
        st.get("abstract", {}).get("text", ""),
        cfg,
    )
    st["checks"] = chk.to_dict()
    mismatch_stop = (
        bool(units_data.get("filename_mismatch"))
        and cfg.get("layout", {}).get("on_mismatch", "stop") == "stop"
        and not ignore_mismatch
    )
    if not chk.ok or mismatch_stop or not (st.get("abstract") or {}).get("text"):
        st["status"] = STATUS_PENDING
    else:
        st["status"] = STATUS_DRAFT
    return st


class Pipeline:
    """Điều phối toàn bộ quy trình AI xử lý file .docx:
    inspect -> label -> extract -> abstract -> judge.
    """

    def __init__(self, cfg: dict, client: BaseClient, renderer: PromptRenderer | None = None):
        self.cfg = cfg
        self.client = client
        self.renderer = renderer or PromptRenderer(cfg.prompts_dir if hasattr(cfg, "prompts_dir") else cfg["paths"]["prompts"])

    def process(
        self,
        docx_path: str | Path,
        *,
        force: bool = False,
        only_step: str | None = None,
        ignore_mismatch: bool = False,
        restart: bool = False,
        cancel_event: threading.Event | None = None,
    ) -> dict:
        """Xử lý một file .docx qua pipeline."""
        return process_file(
            Path(docx_path),
            self.cfg,
            self.client,
            force=force,
            only_step=only_step,
            ignore_mismatch=ignore_mismatch,
            restart=restart,
            renderer=self.renderer,
            cancel_event=cancel_event,
        )

    async def process_async(
        self,
        docx_path: str | Path,
        *,
        force: bool = False,
        only_step: str | None = None,
        ignore_mismatch: bool = False,
        restart: bool = False,
        cancel_event: threading.Event | None = None,
    ) -> dict:
        """Xử lý bất đồng bộ (asyncio) một file .docx qua pipeline."""
        import asyncio

        return await asyncio.to_thread(
            self.process,
            docx_path,
            force=force,
            only_step=only_step,
            ignore_mismatch=ignore_mismatch,
            restart=restart,
            cancel_event=cancel_event,
        )

    async def process_batch_async(
        self,
        docx_paths: list[str | Path],
        *,
        max_concurrency: int = 4,
        force: bool = False,
        only_step: str | None = None,
    ) -> list[dict]:
        """Xử lý bất đồng bộ nhiều file .docx song song với giới hạn concurrency."""
        import asyncio

        if not docx_paths:
            return []
        sem = asyncio.Semaphore(max(1, max_concurrency))

        async def _one(p: str | Path) -> dict:
            async with sem:
                return await self.process_async(p, force=force, only_step=only_step)

        return list(await asyncio.gather(*(_one(p) for p in docx_paths)))


def process_file(docx_path: Path, cfg: dict, client: BaseClient, *,
                 force: bool = False, only_step: str | None = None,
                 ignore_mismatch: bool = False, restart: bool = False,
                 renderer: PromptRenderer | None = None,
                 cancel_event: threading.Event | None = None) -> dict:
    """Xử lý một file .docx (toàn bộ pipeline hoặc chỉ một bước)."""
    ws = FileWorkspace(cfg.work_dir, docx_path)
    st = ws.load_state()
    if not force and not only_step and st["status"] in DONE_STATUSES:
        return {"skipped": True, "state": st}

    eff_cancel = cancel_event if cancel_event is not None else get_active_cancel_event()
    prev_status = st.get("status")
    had_local_edits = ws.differs_from_latest_version(st)
    mutated_by_ai = False

    if renderer is None:
        renderer = PromptRenderer(cfg.prompts_dir)
    # Xóa thông báo lỗi cũ trong flags mỗi khi bắt đầu chạy lại
    old_err = st.get("error_message")
    st["flags"] = [
        f for f in (st.get("flags") or [])
        if f != old_err
        and not f.startswith("Gọi Gemini thất bại")
        and not f.startswith("Bỏ qua bước tự chấm G-Eval")
        and "UndefinedError" not in f
        and "APITimeoutError" not in f
        and "TimeoutError" not in f
        and "ReadTimeout" not in f
        and "ConnectTimeout" not in f
        and "InternalServerError" not in f
        and "RateLimitError" not in f
        and "Error:" not in f
        and "Exception:" not in f
    ]
    st["error_message"] = None
    if st.get("status") == STATUS_ERROR:
        st["status"] = STATUS_PENDING
    st["current_step"] = only_step or "inspect"
    ws.save_state(st)
    pre_ai_backed_up = False

    def _backup_before_ai(step_label: str) -> None:
        nonlocal pre_ai_backed_up
        if pre_ai_backed_up:
            return
        pre_ai_backed_up = True
        if ws.differs_from_latest_version(st):
            ws.save_draft_version(
                st,
                source="auto:before_ai",
                note=f"Tự động lưu trước khi AI chạy lại ({step_label})",
            )

    try:
        with bind_cancel_event(eff_cancel):
            check_not_cancelled(eff_cancel)
            if only_step:
                valid_only_steps = (*STEPS_ORDER, "extract_abstract")
                if only_step not in valid_only_steps:
                    raise ValueError(f"Bước không hợp lệ: {only_step}. Chọn: {valid_only_steps}")
                units_data = ws.load_units()
                if only_step == "inspect" or units_data is None:
                    check_not_cancelled(eff_cancel)
                    st["current_step"] = "inspect"
                    ws.save_state(st)
                    units_data = step_inspect(ws, cfg, st)
                if only_step != "inspect":
                    check_not_cancelled(eff_cancel)
                    if only_step in ("extract", "extract_abstract", "abstract", "judge"):
                        sync_manual_label_metadata(
                            st,
                            (st.get("label") or {}).get("value", ""),
                            cfg,
                        )
                    _backup_before_ai(only_step)
                prev_label = nfc(str((st.get("label") or {}).get("value") or "")).strip()
                label_changed = False
                new_label = prev_label
                if only_step == "label":
                    check_not_cancelled(eff_cancel)
                    st["current_step"] = "label"
                    ws.save_state(st)
                    step_label(ws, units_data, client, renderer, cfg, st)
                    mutated_by_ai = True
                    new_label = nfc(str((st.get("label") or {}).get("value") or "")).strip()
                    differs = new_label != prev_label
                    newly_marked: list[str] = []
                    if differs:
                        reason = (
                            f"Nhãn phong cách đã thay đổi từ '{prev_label}' sang '{new_label}'"
                            if prev_label
                            else f"Đã gán nhãn phong cách '{new_label}'"
                        )
                        newly_marked = _mark_stale_after(st, "label", reason=reason)
                    label_changed = bool(differs and (prev_label or newly_marked))
                elif only_step == "extract":
                    check_not_cancelled(eff_cancel)
                    st["current_step"] = "extract"
                    ws.save_state(st)
                    step_extract(ws, units_data, client, renderer, cfg, st)
                    mutated_by_ai = True
                    _mark_stale_after(st, "extract", reason="Bản trích chọn vừa được AI tạo lại")
                elif only_step == "extract_abstract":
                    if not (st.get("label") or {}).get("value"):
                        check_not_cancelled(eff_cancel)
                        st["current_step"] = "label"
                        ws.save_state(st)
                        step_label(ws, units_data, client, renderer, cfg, st)
                        mutated_by_ai = True
                    check_not_cancelled(eff_cancel)
                    st["current_step"] = "extract"
                    ws.save_state(st)
                    step_extract(ws, units_data, client, renderer, cfg, st)
                    mutated_by_ai = True
                    check_not_cancelled(eff_cancel)
                    st["current_step"] = "abstract"
                    ws.save_state(st)
                    step_abstract(ws, units_data, client, renderer, cfg, st)
                    _mark_stale_after(st, "abstract", reason="Bản trích chọn và Bản biên soạn vừa được AI tạo lại")
                elif only_step == "abstract":
                    if not st["label"]["value"]:
                        check_not_cancelled(eff_cancel)
                        st["current_step"] = "label"
                        ws.save_state(st)
                        step_label(ws, units_data, client, renderer, cfg, st)
                        mutated_by_ai = True
                    if not st["extract"]["text"]:
                        check_not_cancelled(eff_cancel)
                        st["current_step"] = "extract"
                        ws.save_state(st)
                        step_extract(ws, units_data, client, renderer, cfg, st)
                        mutated_by_ai = True
                    check_not_cancelled(eff_cancel)
                    st["current_step"] = "abstract"
                    ws.save_state(st)
                    step_abstract(ws, units_data, client, renderer, cfg, st)
                    mutated_by_ai = True
                    _mark_stale_after(st, "abstract", reason="Bản biên soạn vừa được AI viết lại")
                elif only_step == "judge":
                    check_not_cancelled(eff_cancel)
                    st["current_step"] = "judge"
                    ws.save_state(st)
                    step_judge(ws, units_data, client, renderer, cfg, st)
                    mutated_by_ai = True
                    _mark_stale_after(st, "judge")
                else:
                    _mark_stale_after(st, only_step)
                if (st.get("extract") or {}).get("text") and (st.get("abstract") or {}).get("text"):
                    finalize_status(units_data, st, cfg, ignore_mismatch=ignore_mismatch)
                st["current_step"] = None
                if only_step != "inspect":
                    ws.save_draft_version(st, source=f"ai:{only_step}")
                ws.save_state(st)
                res_out: dict = {"skipped": False, "state": st}
                if only_step == "label":
                    res_out.update({
                        "label_changed": label_changed,
                        "previous_label": prev_label,
                        "new_label": new_label,
                    })
                return res_out

            # Chạy toàn bộ pipeline: tự động kế thừa thông minh những bước đã làm xong đạt chuẩn,
            # chỉ gọi AI cho những bước còn thiếu hoặc cần sửa, trừ khi có yêu cầu restart từ đầu.
            if restart:
                _backup_before_ai("cả bài")
                mutated_by_ai = True
                defaults = ws.default_state()
                st["status"] = STATUS_PENDING
                st["label"] = defaults["label"]
                st["extract"] = defaults["extract"]
                st["abstract"] = defaults["abstract"]
                st["judge"] = None
                st["checks"] = None
                st["flags"] = []
                st["stale_steps"] = []
                st["stale_reason"] = None
                st["model_used"] = ""
                ws.save_state(st)

            # Tải units_data đã tách câu (nếu chưa có hoặc đang restart từ đầu thì gọi step_inspect)
            units_data = None if (restart and ws.docx_path.exists()) else ws.load_units()
            if units_data is None:
                check_not_cancelled(eff_cancel)
                st["current_step"] = "inspect"
                ws.save_state(st)
                units_data = step_inspect(ws, cfg, st)

            if (
                units_data.get("filename_mismatch")
                and cfg.get("layout", {}).get("on_mismatch", "stop") == "stop"
                and not ignore_mismatch
            ):
                st["status"] = STATUS_PENDING
                st["current_step"] = None
                ws.save_state(st)
                return {"skipped": False, "state": st}

            ran_ai = False

            # Bước 1: Gán nhãn phong cách chức năng (label)
            has_valid_label = bool(
                (st.get("label") or {}).get("value") in STYLES
                and (st.get("label") or {}).get("confidence")
            )
            if restart or not has_valid_label:
                check_not_cancelled(eff_cancel)
                _backup_before_ai("label")
                st["label"] = {}  # Nếu bước trước làm dở/chưa xong 100%, hủy bỏ và làm lại từ đầu bước này
                mutated_by_ai = True
                st["current_step"] = "label"
                ws.save_state(st)
                step_label(ws, units_data, client, renderer, cfg, st)
                ran_ai = True
                has_valid_label = bool(
                    (st.get("label") or {}).get("value") in STYLES
                    and (st.get("label") or {}).get("confidence")
                )

            # Bước 2: Trích chọn câu (extract)
            # 100% hoàn thành nghĩa là: có text, selections hợp lệ, words > 0, và không có lỗi error
            prev_ext_text = (st.get("extract") or {}).get("text", "")
            prev_ext_sels = (st.get("extract") or {}).get("selections") or {}
            prev_ext_words = (st.get("extract") or {}).get("words", 0)
            has_valid_extract = False
            if prev_ext_text and prev_ext_sels and prev_ext_words > 0 and has_valid_label:
                chk_prev = run_checks(units_data, st["label"]["value"], prev_ext_text, "ok.", cfg)
                has_valid_extract = len(chk_prev.of(level="error", target="extract")) == 0

            if restart or not has_valid_extract:
                check_not_cancelled(eff_cancel)
                _backup_before_ai("extract")
                st["extract"] = {}  # Nếu bước trước làm dở/chưa xong 100%, hủy bỏ và làm lại từ đầu bước này
                mutated_by_ai = True
                st["current_step"] = "extract"
                ws.save_state(st)
                step_extract(ws, units_data, client, renderer, cfg, st)
                ran_ai = True
                prev_ext_text = (st.get("extract") or {}).get("text", "")
                prev_ext_sels = (st.get("extract") or {}).get("selections") or {}
                prev_ext_words = (st.get("extract") or {}).get("words", 0)
                has_valid_extract = bool(prev_ext_text and prev_ext_sels and prev_ext_words > 0)

            # Bước 3: Biên soạn tóm tắt (abstract)
            # 100% hoàn thành nghĩa là: có text, words > 0, và chk_abs.ok
            prev_abs_text = (st.get("abstract") or {}).get("text", "")
            prev_abs_words = (st.get("abstract") or {}).get("words", 0)
            has_valid_abstract = False
            if prev_abs_text and prev_abs_words > 0 and has_valid_label and has_valid_extract:
                chk_abs = run_checks(units_data, st["label"]["value"], st["extract"]["text"], prev_abs_text, cfg)
                has_valid_abstract = chk_abs.ok
                if not has_valid_abstract:
                    # Nếu abstract cũ có lỗi cơ học, gửi lỗi cũ để AI sửa
                    check_not_cancelled(eff_cancel)
                    _backup_before_ai("abstract")
                    st["current_step"] = "abstract"
                    ws.save_state(st)
                    fb = retry_feedback(chk_abs, cfg, target="abstract")
                    err_list = fb or [i.message for i in chk_abs.errors]
                    _generate_abstract_loop(
                        ws, units_data, client, renderer, cfg, st,
                        extra_errors=err_list,
                    )
                    mutated_by_ai = True
                    ran_ai = True
                else:
                    st["checks"] = chk_abs.to_dict()
            else:
                check_not_cancelled(eff_cancel)
                _backup_before_ai("abstract")
                st["abstract"] = {}  # Nếu bước trước làm dở/chưa xong 100%, hủy bỏ và làm lại từ đầu bước này
                mutated_by_ai = True
                st["current_step"] = "abstract"
                ws.save_state(st)
                step_abstract(ws, units_data, client, renderer, cfg, st)
                ran_ai = True

            # Bước 4: Tự chấm G-Eval (nếu được bật, chưa có điểm và đạt ngưỡng kiểm tra cơ học khi bật gatekeeper)
            j_cfg = cfg.get("judge", {})
            if j_cfg.get("enabled", False) and (restart or not st.get("judge")):
                chk_cur = st.get("checks")
                if chk_cur is None:
                    chk_cur = finalize_status(units_data, st, cfg, ignore_mismatch=ignore_mismatch).get("checks")
                checks_passed = bool((chk_cur or {}).get("ok"))
                if j_cfg.get("only_if_checks_ok", True) and not checks_passed:
                    pass  # Không đốt lượt gọi G-Eval cho bản nháp vẫn còn lỗi cơ học
                else:
                    try:
                        check_not_cancelled(eff_cancel)
                        _backup_before_ai("judge")
                        st["current_step"] = "judge"
                        ws.save_state(st)
                        step_judge(ws, units_data, client, renderer, cfg, st)
                        mutated_by_ai = True
                        ran_ai = True
                    except LLMError as judge_exc:
                        _add_flag(st, f"Bỏ qua bước tự chấm G-Eval ({judge_exc}).")

            finalize_status(units_data, st, cfg, ignore_mismatch=ignore_mismatch)
            st["current_step"] = None
            if ran_ai or ws.differs_from_latest_version(st):
                ws.save_draft_version(st, source="ai:pipeline")
            ws.save_state(st)
            return {"skipped": False, "state": st}
    except LLMCancelledError:
        st["current_step"] = None
        st["error_message"] = None
        if not mutated_by_ai and not had_local_edits and prev_status in (STATUS_APPROVED, STATUS_EXPORTED):
            st["status"] = prev_status
        elif (st.get("extract") or {}).get("text") and (st.get("abstract") or {}).get("text"):
            u_cur = ws.load_units()
            if u_cur:
                finalize_status(u_cur, st, cfg, ignore_mismatch=ignore_mismatch)
            else:
                st["status"] = STATUS_PENDING
        else:
            st["status"] = STATUS_PENDING
        ws.save_state(st)
        return {"skipped": False, "cancelled": True, "state": st}
    except (DocxError, LLMError) as exc:
        st["status"] = STATUS_PENDING if isinstance(exc, DocxError) else STATUS_ERROR
        st["current_step"] = None
        st["error_message"] = str(exc)
        _add_flag(st, str(exc))
        ws.save_state(st)
        return {"skipped": False, "state": st, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        st["status"] = STATUS_ERROR
        st["current_step"] = None
        st["error_message"] = f"{type(exc).__name__}: {exc}"
        _add_flag(st, st["error_message"])
        ws.save_state(st)
        return {"skipped": False, "state": st, "error": st["error_message"]}
