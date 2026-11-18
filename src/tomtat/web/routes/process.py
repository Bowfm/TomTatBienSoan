"""Blueprint: process — xử lý AI hàng loạt và chạy lại file."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from ...config import load_config
from ...pipeline import process_file
from ...report import write_reports
from ...state import FileWorkspace
from ...textutil import count_words, nfc
from ..batch import BatchManager, make_client, resolve_files

bp = Blueprint("process", __name__)

# BatchManager được tạo một lần trong create_app() và gắn vào app.config
def _cfg():
    return current_app.config["TOMTAT_CFG"]

def _mock():
    return current_app.config.get("TOMTAT_MOCK", False)

def _batch_mgr() -> BatchManager:
    return current_app.config["TOMTAT_BATCH_MGR"]


def _sync_config():
    try:
        fresh = load_config()
        cfg = _cfg()
        for k in ("extract", "abstract", "grading", "gemini", "word_count",
                  "sentence_split", "judge", "workers"):
            if k in fresh:
                cfg[k] = fresh[k]
    except Exception:
        pass


@bp.post("/api/batch/start")
def api_batch_start():
    payload = request.get_json(force=True) or {}
    stems = payload.get("stems") or []
    concurrency = int(payload.get("concurrency") or 8)
    use_mock = bool(payload.get("mock", _mock()))
    return jsonify(_batch_mgr().start(stems, concurrency, use_mock))


@bp.get("/api/batch/status")
def api_batch_status():
    return jsonify(_batch_mgr().status())


@bp.get("/api/batch/events")
def api_batch_events():
    """Server-Sent Events (SSE) stream để cập nhật tiến độ batch theo thời gian thực."""
    import json
    import time
    from flask import Response

    mgr = _batch_mgr()

    def stream():
        last_dump = None
        while True:
            st = mgr.status()
            dump = json.dumps(st)
            if dump != last_dump:
                last_dump = dump
                yield f"data: {dump}\n\n"
            if not st["active"]:
                break
            time.sleep(0.5)

    return Response(stream(), mimetype="text/event-stream")


@bp.post("/api/batch/stop")
def api_batch_stop():
    return jsonify(_batch_mgr().stop())


@bp.post("/api/process-batch")
def api_process_batch():
    """Chạy AI cho tất cả các file chưa xử lý (hoặc danh sách chỉ định) ngay từ trình duyệt."""
    cfg = _cfg()
    payload = request.get_json(force=True) or {}
    use_mock = bool(payload.get("mock", _mock()))
    force = bool(payload.get("force", False))
    stems = payload.get("stems") or []

    try:
        _sync_config()
        client = make_client(cfg, force_mock=use_mock)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400

    files_map = resolve_files(cfg)
    targets = (
        [files_map[s] for s in stems if s in files_map]
        if stems
        else list(files_map.values())
    )
    ran, skipped, errs = [], [], []
    for p in targets:
        res = process_file(p, cfg, client, force=force)
        if res.get("skipped"):
            skipped.append(p.name)
        else:
            ran.append(p.name)
        if res.get("error"):
            errs.append(f"{p.name}: {res['error']}")
    write_reports(cfg)
    return jsonify({
        "ok": True,
        "ran": ran,
        "skipped": skipped,
        "errors": errs,
    })


@bp.post("/api/file/<stem>/rerun")
def api_rerun(stem: str):
    """Chạy lại một bước (hoặc toàn bộ) bằng AI kèm ghi chú của người duyệt."""
    cfg = _cfg()
    files_map = resolve_files(cfg)
    if stem not in files_map:
        return jsonify({"error": f"Không tìm thấy file '{stem}'."}), 404
    docx_path = files_map[stem]
    payload = request.get_json(force=True) or {}
    step = payload.get("step") or None
    user_note = nfc(payload.get("note", "")).strip()
    use_mock = bool(payload.get("mock", _mock()))
    ignore_mismatch = bool(payload.get("ignore_mismatch", False))
    restart = bool(payload.get("restart", False))

    # Lưu tạm các chỉnh sửa trên màn hình trước khi chạy bước tiếp theo
    ws = FileWorkspace(cfg.work_dir, docx_path)
    st = ws.load_state()
    if payload.get("style"):
        st.setdefault("label", {})["value"] = nfc(payload["style"]).strip()
    if payload.get("extract") is not None and step in ("abstract", "judge"):
        st.setdefault("extract", {})["text"] = nfc(payload["extract"])
        st["extract"]["words"] = count_words(st["extract"]["text"])
    if payload.get("abstract") is not None and step == "judge":
        st.setdefault("abstract", {})["text"] = nfc(payload["abstract"])
        st["abstract"]["words"] = count_words(st["abstract"]["text"])
    ws.save_state(st)

    try:
        _sync_config()
        client = make_client(cfg, force_mock=use_mock)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400

    out = process_file(
        docx_path,
        cfg,
        client,
        force=True,
        only_step=step if step != "all" else None,
        user_note=user_note,
        ignore_mismatch=ignore_mismatch,
        restart=restart,
    )
    write_reports(cfg)
    if out.get("error"):
        return jsonify({"error": out["error"], "state": out["state"]}), 400
    return jsonify({
        "ok": True,
        "state": out["state"],
        "units": ws.load_units(),
        "versions": ws.list_draft_versions(),
    })
