"""Blueprint: files — quản lý file (danh sách, tải lên, đọc, logs)."""
from __future__ import annotations

import re
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from ...checks import run_checks
from ...config import get_api_key, get_api_keys, load_config
from ...docx_io import DocxError
from ...memory import load_memory
from ...pipeline import step_inspect
from ...report import _infer_model_from_logs, collect_rows, infer_step_models_from_logs, write_reports
from ...state import FileWorkspace
from ...textutil import nfc
from ..batch import make_client, resolve_files

bp = Blueprint("files", __name__)


def _cfg():
    return current_app.config["TOMTAT_CFG"]


def _mock():
    return current_app.config.get("TOMTAT_MOCK", False)


def _sync_config():
    """Reload config non-paths từ disk vào cfg live."""
    try:
        fresh = load_config()
        cfg = _cfg()
        for k in ("extract", "abstract", "grading", "gemini", "word_count",
                  "sentence_split", "judge", "workers"):
            if k in fresh:
                cfg[k] = fresh[k]
    except Exception:
        pass


@bp.get("/api/files")
def api_files():
    cfg = _cfg()
    fresh_cfg = load_config()
    files_map = resolve_files(cfg)
    rows = collect_rows(cfg, list(files_map.values()))
    g = fresh_cfg.get("gemini", {})
    mem = load_memory(cfg.work_dir)
    from ... import STYLES
    from ...state import STATUS_VI
    return jsonify({
        "files": rows,
        "styles": STYLES,
        "status_vi": STATUS_VI,
        "mock": _mock(),
        "has_api_key": bool(get_api_key()),
        "api_key_count": len(get_api_keys()),
        "primary_model": g.get("model", ""),
        "fallback_models": g.get("fallback_models") or [],
        "learned_count": mem.get("total_approved_learned", 0),
    })


@bp.post("/api/upload")
def api_upload():
    """Tải một hoặc nhiều file .docx từ trình duyệt vào thư mục input\\ (và tùy chọn chạy AI ngay)."""
    cfg = _cfg()
    uploaded = request.files.getlist("files")
    if not uploaded:
        return jsonify({"error": "Chưa chọn file .docx nào."}), 400
    run_now = request.form.get("run_now", "false").lower() in ("1", "true", "yes", "on")
    use_mock = request.form.get("mock", "false").lower() in ("1", "true", "yes", "on") or _mock()
    overwrite = request.form.get("overwrite", "false").lower() in ("1", "true", "yes", "on")

    uploaded.sort(
        key=lambda f: [int(t) if t.isdigit() else t.lower()
                       for t in re.split(r"(\d+)", str(getattr(f, "filename", "") or ""))],
        reverse=True,
    )

    cfg.input_dir.mkdir(parents=True, exist_ok=True)
    saved_paths: list[Path] = []
    skipped_existing: list[str] = []
    errors: list[str] = []

    for f in uploaded:
        raw_name = Path(nfc(f.filename or "")).name.strip()
        if not raw_name or not raw_name.lower().endswith(".docx") or raw_name.startswith("~$"):
            errors.append(f"Bỏ qua '{raw_name or '(trống)'}': chỉ nhận file .docx hợp lệ.")
            continue
        dst = cfg.input_dir / raw_name
        if dst.exists() and not overwrite:
            skipped_existing.append(raw_name)
            continue
        f.save(str(dst))
        saved_paths.append(dst)

    if skipped_existing and not overwrite:
        return jsonify({
            "need_overwrite": True,
            "existing": skipped_existing,
            "added": [p.name for p in saved_paths],
            "message": (
                f"Có {len(skipped_existing)} file đã tồn tại trong danh sách "
                f"({', '.join(skipped_existing[:5])}{'...' if len(skipped_existing) > 5 else ''}). "
                "Bạn có muốn ghi đè và làm lại không?"
            ),
        })

    processed_stems: list[str] = []
    for dst in saved_paths:
        ws = FileWorkspace(cfg.work_dir, dst)
        st = ws.load_state()
        if overwrite:
            st = ws.default_state()
        try:
            step_inspect(ws, cfg, st)
        except DocxError as exc:
            errors.append(f"{dst.name}: {exc}")
            continue
        processed_stems.append(dst.stem)

    processed_stems.sort()

    if run_now and processed_stems:
        from ...pipeline import process_file
        try:
            client = make_client(cfg, force_mock=use_mock)
            for stem in processed_stems:
                dst = cfg.input_dir / f"{stem}.docx"
                res = process_file(dst, cfg, client, force=True)
                if res.get("error"):
                    errors.append(f"{dst.name}: {res['error']}")
        except RuntimeError as exc:
            errors.append(str(exc))

    write_reports(cfg)
    return jsonify({
        "ok": True,
        "added": sorted([p.name for p in saved_paths]),
        "stems": processed_stems,
        "skipped_existing": skipped_existing,
        "errors": errors,
    })


@bp.get("/api/file/<stem>")
def api_get_file(stem: str):
    cfg = _cfg()
    _sync_config()
    files_map = resolve_files(cfg)
    if stem not in files_map:
        return jsonify({"error": f"Không tìm thấy file '{stem}'."}), 404
    docx_path = files_map[stem]
    ws = FileWorkspace(cfg.work_dir, docx_path)
    st = ws.load_state()
    units_data = ws.load_units()
    if units_data is None and docx_path.exists():
        try:
            units_data = step_inspect(ws, cfg, st)
        except DocxError as exc:
            return jsonify({"error": str(exc)}), 400
    if units_data:
        from ...units import compute_budget
        if "total_words" in (units_data.get("budget") or {}):
            units_data["budget"] = compute_budget(units_data["budget"]["total_words"], cfg)
        chk = run_checks(
            units_data,
            st.get("label", {}).get("value", ""),
            st.get("extract", {}).get("text", ""),
            st.get("abstract", {}).get("text", ""),
            cfg,
        )
        st["checks"] = chk.to_dict()
    step_models = infer_step_models_from_logs(ws, st)
    for step_name, m_name in step_models.items():
        if isinstance(st.get(step_name), dict) and not st[step_name].get("model_used"):
            st[step_name]["model_used"] = m_name
    if not st.get("model_used"):
        inferred = _infer_model_from_logs(ws, st)
        if inferred:
            st["model_used"] = inferred
    return jsonify({
        "state": st,
        "units": units_data,
        "versions": ws.list_draft_versions(),
    })


@bp.get("/api/file/<stem>/logs")
def api_get_logs(stem: str):
    """Trả về danh sách các lần gọi Gemini (logs) của file để xem trực tiếp trên web."""
    cfg = _cfg()
    ws = FileWorkspace(cfg.work_dir, cfg.input_dir / f"{stem}.docx")
    items = []
    if ws.logs_dir.exists():
        import json
        for lp in sorted(ws.logs_dir.glob("*.json"), reverse=True)[:40]:
            try:
                d = json.loads(lp.read_text(encoding="utf-8"))
                items.append({
                    "filename": lp.name,
                    "started_at": d.get("started_at", ""),
                    "step": d.get("step", ""),
                    "attempt": d.get("attempt", 1),
                    "net_try": d.get("net_try", 0),
                    "model": d.get("model", ""),
                    "duration_s": d.get("duration_s", 0),
                    "usage": d.get("usage") or {},
                    "error": d.get("error", ""),
                    "response_text": d.get("response_text", ""),
                    "prompt": d.get("prompt", ""),
                })
            except Exception:  # noqa: BLE001
                pass
    return jsonify({"logs": items, "log_dir": str(ws.logs_dir)})
