"""Pipeline xử lý từng file .docx: inspect -> label -> extract -> abstract -> judge."""
from __future__ import annotations

from pathlib import Path

from . import STYLES
from .checks import estimate_rouge2, needs_retry, quality_key, retry_feedback, run_checks
from .docx_io import DocxError
from .llm import BaseClient, LLMError
from .memory import format_label_memory, format_style_memory
from .prompts import ABSTRACT_SCHEMA, EXTRACT_SCHEMA, JUDGE_SCHEMA, LABEL_SCHEMA, PromptRenderer
from .state import (
    DONE_STATUSES,
    STATUS_DRAFT,
    STATUS_ERROR,
    STATUS_MANUAL,
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


def _add_flag(st: dict, flag: str) -> None:
    flags = st.setdefault("flags", [])
    if flag not in flags:
        flags.append(flag)


def _remove_flag(st: dict, flag: str) -> None:
    flags = st.get("flags") or []
    if flag in flags:
        flags.remove(flag)


def _mark_stale_after(st: dict, step: str) -> None:
    if step not in STEPS_ORDER:
        return
    idx = STEPS_ORDER.index(step)
    stale = set(st.get("stale_steps") or [])
    for s in STEPS_ORDER[idx + 1:]:
        stale.add(s)
    stale.discard(step)
    st["stale_steps"] = [s for s in STEPS_ORDER if s in stale]


def _clear_stale(st: dict, step: str) -> None:
    stale = st.get("stale_steps") or []
    if step in stale:
        stale.remove(step)
    st["stale_steps"] = stale


# ------------------------------------------------------------------ Bước 0: inspect

def step_inspect(ws: FileWorkspace, cfg: dict, st: dict) -> dict:
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
            st["status"] = STATUS_MANUAL
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

def step_label(ws: FileWorkspace, units_data: dict, client: BaseClient,
               renderer: PromptRenderer, cfg: dict, st: dict,
               user_note: str = "") -> None:
    prompt = renderer.render(
        "label.md",
        docs=units_data["docs"],
        source_text=source_text(units_data),
        user_note=user_note,
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
    _remove_flag(st, "Cần xem kỹ nhãn phong cách (mức chắc chắn thấp/trung bình hoặc lệch đa số).")
    if conf not in ok_conf or (raw_label in STYLES and majority in STYLES and raw_label != majority):
        _add_flag(st, "Cần xem kỹ nhãn phong cách (mức chắc chắn thấp/trung bình hoặc lệch đa số).")

    st["label"] = {
        "value": final_label,
        "model_label": raw_label,
        "computed_majority": majority,
        "per_doc": per_doc_clean,
        "confidence": conf,
        "reason": nfc(out.get("reason", "")).strip(),
        "model_used": res.model,
    }
    st["model_used"] = res.model
    _clear_stale(st, "label")
    ws.save_state(st)


# ------------------------------------------------------------------ Bước 2: extract

def step_extract(ws: FileWorkspace, units_data: dict, client: BaseClient,
                 renderer: PromptRenderer, cfg: dict, st: dict,
                 user_note: str = "") -> None:
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
        prompt = renderer.render(
            "extract.md",
            docs=units_data["docs"],
            budget=budget,
            query=units_data.get("query", ""),
            ai_extract=units_data.get("ai_extract", ""),
            units_listing=units_listing(units_data, with_words=True),
            retry_message=retry_msg,
            user_note=user_note,
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
    _clear_stale(st, "extract")
    ws.save_state(st)


# ------------------------------------------------------------------ Bước 3: abstract

def _generate_abstract_loop(ws: FileWorkspace, units_data: dict, client: BaseClient,
                            renderer: PromptRenderer, cfg: dict, st: dict,
                            user_note: str = "", extra_errors: list[str] | None = None,
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
            user_note=user_note,
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
        abs_text = nfc(res.data.get("abstract", "")).strip()
        n_docs = len(units_data.get("docs", []))
        if n_docs > 1:
            from .checks import clean_multi_doc_abstract
            abs_text = clean_multi_doc_abstract(abs_text, n_docs)
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


def step_abstract(ws: FileWorkspace, units_data: dict, client: BaseClient,
                  renderer: PromptRenderer, cfg: dict, st: dict,
                  user_note: str = "") -> None:
    st.setdefault("abstract", {})["attempts"] = 0
    _generate_abstract_loop(ws, units_data, client, renderer, cfg, st, user_note=user_note)


# ------------------------------------------------------------------ Bước 4: judge (tùy chọn)

def step_judge(ws: FileWorkspace, units_data: dict, client: BaseClient,
               renderer: PromptRenderer, cfg: dict, st: dict,
               allow_rewrite: bool = True) -> None:
    j_cfg = cfg.get("judge", {})
    style = st["label"]["value"] or STYLES[0]
    extract_text = st["extract"]["text"]
    abstract_text = st["abstract"]["text"]

    prompt = renderer.render(
        "judge.md",
        style=style,
        style_guide=renderer.style_guide(style),
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
    scores = [float(d.get(k, 0)) for k in ("relevance", "coherence", "consistency", "fluency")]
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

    if st["judge"]["average"] < min_avg:
        _add_flag(st, f"Điểm G-Eval tự chấm ({st['judge']['average']}) thấp hơn ngưỡng {min_avg}.")
    else:
        _remove_flag(st, f"Điểm G-Eval tự chấm ({st['judge']['average']}) thấp hơn ngưỡng {min_avg}.")
    _clear_stale(st, "judge")
    ws.save_state(st)


# ------------------------------------------------------------------ Điều phối

def finalize_status(units_data: dict, st: dict, cfg: dict,
                    ignore_mismatch: bool = False) -> dict:
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
    if not chk.ok or mismatch_stop:
        st["status"] = STATUS_MANUAL
    else:
        st["status"] = STATUS_DRAFT
    return st


def process_file(docx_path: Path, cfg: dict, client: BaseClient, *,
                 force: bool = False, only_step: str | None = None,
                 user_note: str = "", ignore_mismatch: bool = False,
                 restart: bool = False) -> dict:
    """Xử lý một file .docx (toàn bộ pipeline hoặc chỉ một bước)."""
    ws = FileWorkspace(cfg.work_dir, docx_path)
    st = ws.load_state()
    if not force and not only_step and st["status"] in DONE_STATUSES:
        return {"skipped": True, "state": st}

    renderer = PromptRenderer(cfg.prompts_dir)
    # Xóa thông báo lỗi cũ trong flags mỗi khi bắt đầu chạy lại
    old_err = st.get("error_message")
    st["flags"] = [
        f for f in (st.get("flags") or [])
        if f != old_err
        and not f.startswith("Gọi Gemini thất bại")
        and "UndefinedError" not in f
        and "APITimeoutError" not in f
        and "InternalServerError" not in f
        and "RateLimitError" not in f
    ]
    st["error_message"] = None
    try:
        if only_step:
            if only_step not in STEPS_ORDER:
                raise ValueError(f"Bước không hợp lệ: {only_step}. Chọn: {STEPS_ORDER}")
            units_data = ws.load_units()
            if only_step == "inspect" or units_data is None:
                units_data = step_inspect(ws, cfg, st)
            if only_step == "label":
                step_label(ws, units_data, client, renderer, cfg, st, user_note=user_note)
            elif only_step == "extract":
                step_extract(ws, units_data, client, renderer, cfg, st, user_note=user_note)
            elif only_step == "abstract":
                if not st["label"]["value"]:
                    step_label(ws, units_data, client, renderer, cfg, st)
                if not st["extract"]["text"]:
                    step_extract(ws, units_data, client, renderer, cfg, st)
                step_abstract(ws, units_data, client, renderer, cfg, st, user_note=user_note)
            elif only_step == "judge":
                step_judge(ws, units_data, client, renderer, cfg, st)
            _mark_stale_after(st, only_step)
            if st["extract"]["text"] and st["abstract"]["text"]:
                finalize_status(units_data, st, cfg, ignore_mismatch=ignore_mismatch)
            ws.save_draft_version(st, source=f"ai:{only_step}", note=user_note)
            ws.save_state(st)
            return {"skipped": False, "state": st}

        # Chạy toàn bộ pipeline: tự động kế thừa thông minh những bước đã làm xong đạt chuẩn,
        # chỉ gọi AI cho những bước còn thiếu hoặc cần sửa, trừ khi có yêu cầu restart từ đầu.
        if restart:
            st["label"] = {}
            st["extract"] = {}
            st["abstract"] = {}
            st["judge"] = None
            st["checks"] = {}
            st["flags"] = []

        # Tải units_data đã tách câu (nếu chưa có thì gọi step_inspect)
        units_data = ws.load_units()
        if units_data is None:
            units_data = step_inspect(ws, cfg, st)

        if (
            units_data.get("filename_mismatch")
            and cfg.get("layout", {}).get("on_mismatch", "stop") == "stop"
            and not ignore_mismatch
        ):
            st["status"] = STATUS_MANUAL
            ws.save_state(st)
            return {"skipped": False, "state": st}

        # Bước 1: Gán nhãn phong cách chức năng (label)
        has_valid_label = bool((st.get("label") or {}).get("value") in STYLES)
        if restart or not has_valid_label or (user_note and "nhãn" in user_note.lower()):
            step_label(ws, units_data, client, renderer, cfg, st, user_note=user_note)
            has_valid_label = bool((st.get("label") or {}).get("value") in STYLES)

        # Bước 2: Trích chọn câu (extract)
        prev_ext_text = (st.get("extract") or {}).get("text", "")
        has_valid_extract = False
        if prev_ext_text and has_valid_label:
            chk_prev = run_checks(units_data, st["label"]["value"], prev_ext_text, "ok.", cfg)
            has_valid_extract = len(chk_prev.of(level="error", target="extract")) == 0

        if restart or not has_valid_extract or (user_note and "trích" in user_note.lower()):
            step_extract(ws, units_data, client, renderer, cfg, st, user_note=user_note)
            prev_ext_text = (st.get("extract") or {}).get("text", "")
            has_valid_extract = bool(prev_ext_text)

        # Bước 3: Biên soạn tóm tắt (abstract)
        prev_abs_text = (st.get("abstract") or {}).get("text", "")
        has_valid_abstract = False
        if prev_abs_text and has_valid_label and has_valid_extract:
            chk_abs = run_checks(units_data, st["label"]["value"], st["extract"]["text"], prev_abs_text, cfg)
            has_valid_abstract = chk_abs.ok
            if not has_valid_abstract:
                # Nếu abstract cũ có lỗi cơ học, gửi lỗi cũ để AI sửa
                fb = retry_feedback(chk_abs, cfg, target="abstract")
                err_list = fb or [i.message for i in chk_abs.errors]
                _generate_abstract_loop(
                    ws, units_data, client, renderer, cfg, st,
                    user_note=user_note, extra_errors=err_list,
                )
            else:
                st["checks"] = chk_abs.to_dict()
        else:
            step_abstract(ws, units_data, client, renderer, cfg, st, user_note=user_note)

        # Bước 4: Tự chấm G-Eval (nếu được bật và chưa có điểm)
        if cfg.get("judge", {}).get("enabled", False) and (restart or not st.get("judge")):
            try:
                step_judge(ws, units_data, client, renderer, cfg, st)
            except LLMError as judge_exc:
                _add_flag(st, f"Bỏ qua bước tự chấm G-Eval do quá thời gian chờ ({judge_exc}).")

        finalize_status(units_data, st, cfg, ignore_mismatch=ignore_mismatch)
        ws.save_draft_version(st, source="ai:pipeline", note=user_note)
        ws.save_state(st)
        return {"skipped": False, "state": st}
    except (DocxError, LLMError) as exc:
        st["status"] = STATUS_MANUAL if isinstance(exc, DocxError) else STATUS_ERROR
        st["error_message"] = str(exc)
        _add_flag(st, str(exc))
        ws.save_state(st)
        return {"skipped": False, "state": st, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        st["status"] = STATUS_ERROR
        st["error_message"] = f"{type(exc).__name__}: {exc}"
        _add_flag(st, st["error_message"])
        ws.save_state(st)
        return {"skipped": False, "state": st, "error": st["error_message"]}
