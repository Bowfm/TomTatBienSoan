"""Blueprint: process — xử lý AI hàng loạt và chạy lại file."""
from __future__ import annotations

import json
import time

from flask import Blueprint, Response, current_app, jsonify, request

from ...llm import get_rpd_status
from ...pipeline import sync_manual_label_metadata
from ...services.file_service import FileService
from ...textutil import count_words, nfc
from ..batch import BatchManager, make_client

bp = Blueprint("process", __name__)


def _cfg():
    return current_app.config["TOMTAT_CFG"]


def _mock():
    return current_app.config.get("TOMTAT_MOCK", False)


def _service() -> FileService:
    return FileService(_cfg())


def _batch_mgr() -> BatchManager:
    # BatchManager được tạo một lần trong create_app() và gắn vào app.config
    return current_app.config["TOMTAT_BATCH_MGR"]


@bp.post("/api/batch/start")
def api_batch_start():
    payload = request.get_json(force=True) or {}
    stems = payload.get("stems") or []
    raw_conc = payload.get("concurrency")
    if raw_conc in (None, "", "auto", 0) or isinstance(raw_conc, bool):
        # Để None để BatchManager.start() tự lấy recommended_workers sau khi reload config.yaml
        concurrency = None
    else:
        try:
            parsed = int(raw_conc)
            concurrency = parsed if parsed > 0 else None
        except (TypeError, ValueError):
            concurrency = None
    use_mock = bool(payload.get("mock", _mock()))
    return jsonify(_batch_mgr().start(stems, concurrency, use_mock))


@bp.get("/api/batch/status")
def api_batch_status():
    return jsonify(_batch_mgr().status())


@bp.get("/api/batch/events")
def api_batch_events():
    """Server-Sent Events (SSE) stream để cập nhật tiến độ batch theo thời gian thực."""
    mgr = _batch_mgr()  # lấy trước khi vào generator (generator chạy ngoài app context)

    def stream():
        last_dump = None
        while True:
            st = mgr.status()
            dump = json.dumps(st, ensure_ascii=False)
            if dump != last_dump:
                last_dump = dump
                yield f"data: {dump}\n\n"
            if not st["active"]:
                break
            time.sleep(0.5)

    return Response(
        stream(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@bp.post("/api/batch/stop")
def api_batch_stop():
    return jsonify(_batch_mgr().stop())


@bp.post("/api/process-batch")
def api_process_batch():
    """Chạy AI cho tất cả các file chưa xử lý (hoặc danh sách chỉ định) ngay từ trình duyệt."""
    cfg = _cfg()
    svc = _service()
    payload = request.get_json(force=True) or {}
    use_mock = bool(payload.get("mock", _mock()))
    force = bool(payload.get("force", False))
    stems = payload.get("stems") or []

    try:
        cfg.reload()
        client = make_client(cfg, force_mock=use_mock)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 400

    files_map = svc.resolve_files()
    targets = (
        [files_map[s] for s in stems if s in files_map]
        if stems
        else list(files_map.values())
    )
    ran, skipped, errs = [], [], []
    mgr = _batch_mgr()
    for p in targets:
        acquired, err_msg = mgr.try_acquire_file(p.stem, "all")
        if not acquired:
            skipped.append(p.name)
            errs.append(err_msg or f"{p.name}: đang được xử lý.")
            continue
        try:
            res = svc.process(p, client, force=force)
        finally:
            mgr.release_file(p.stem)
        if res.get("skipped"):
            skipped.append(p.name)
        else:
            ran.append(p.name)
        if res.get("error"):
            errs.append(f"{p.name}: {res['error']}")
    svc.write_reports()
    return jsonify({
        "ok": True,
        "ran": ran,
        "skipped": skipped,
        "errors": errs,
    })


@bp.post("/api/file/<stem>/rerun")
def api_rerun(stem: str):
    """Chạy lại một bước (hoặc toàn bộ) bằng AI."""
    cfg = _cfg()
    svc = _service()
    files_map = svc.resolve_files()
    if stem not in files_map:
        return jsonify({"error": f"Không tìm thấy file '{stem}'."}), 404
    docx_path = files_map[stem]
    payload = request.get_json(force=True) or {}
    step = payload.get("step") or None
    use_mock = bool(payload.get("mock", _mock()))
    ignore_mismatch = bool(payload.get("ignore_mismatch", False))
    # Khi chạy lại cả bài (step == "all" hoặc None), mặc định phải chạy lại từ đầu (restart=True)
    # trừ khi caller truyền tường minh `"restart": false`.
    restart = bool(payload["restart"]) if "restart" in payload else (step in (None, "all"))

    mgr = _batch_mgr()
    acquired, err_msg = mgr.try_acquire_file(stem, step or "all")
    if not acquired:
        return jsonify({"error": err_msg, "already_running": True}), 409

    cancel_ev = mgr.get_cancel_event(stem)
    try:
        try:
            cfg.reload()
            client = make_client(cfg, force_mock=use_mock)
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 400

        if cancel_ev is not None and cancel_ev.is_set():
            rpd = get_rpd_status(cfg)
            detail = svc.get_file_detail(stem)
            return jsonify({
                "ok": False,
                "cancelled": True,
                "message": f"Đã dừng chạy AI cho {stem}.docx theo yêu cầu.",
                "state": detail["state"],
                "units": detail["units"],
                "versions": detail["versions"],
                "rpd_status": rpd,
            })

        # Lưu tạm các chỉnh sửa trên màn hình trước khi chạy bước tiếp theo
        # (không ghi đè state cũ khi chuẩn bị chạy lại từ bước label hoặc restart cả bài)
        if step in ("extract", "extract_abstract", "abstract", "judge") or (step in (None, "all") and not restart):
            ws = svc.get_workspace(docx_path)
            st = ws.load_state()
            changed = False
            if payload.get("style"):
                if sync_manual_label_metadata(st, payload["style"], cfg):
                    changed = True
            if payload.get("extract") is not None and step in ("abstract", "judge"):
                st.setdefault("extract", {})["text"] = nfc(payload["extract"])
                st["extract"]["words"] = count_words(st["extract"]["text"])
                changed = True
            if payload.get("abstract") is not None and step == "judge":
                st.setdefault("abstract", {})["text"] = nfc(payload["abstract"])
                st["abstract"]["words"] = count_words(st["abstract"]["text"])
                changed = True
            if changed:
                ws.save_state(st)

        out = svc.process(
            docx_path,
            client,
            force=True,
            only_step=step if step != "all" else None,
            ignore_mismatch=ignore_mismatch,
            restart=restart,
            cancel_event=cancel_ev,
        )
        svc.write_reports()
        rpd = get_rpd_status(cfg)
        detail = svc.get_file_detail(stem)
        if out.get("cancelled"):
            return jsonify({
                "ok": False,
                "cancelled": True,
                "message": f"Đã dừng chạy AI cho {stem}.docx theo yêu cầu.",
                "state": detail["state"],
                "units": detail["units"],
                "versions": detail["versions"],
                "rpd_status": rpd,
            })
        if out.get("error"):
            return jsonify({
                "error": out["error"],
                "state": detail["state"],
                "units": detail["units"],
                "versions": detail["versions"],
                "rpd_status": rpd,
            }), 400
        resp: dict = {
            "ok": True,
            "state": detail["state"],
            "units": detail["units"],
            "versions": detail["versions"],
            "rpd_status": rpd,
        }
        if step == "label":
            resp["label_changed"] = bool(out.get("label_changed"))
            resp["previous_label"] = out.get("previous_label", "")
            resp["new_label"] = out.get("new_label", "")
        return jsonify(resp)
    finally:
        mgr.release_file(stem)


@bp.post("/api/file/<stem>/stop")
def api_stop_file(stem: str):
    """Dừng tiến trình AI đang chạy cho một file cụ thể (chạy đơn lẻ hoặc trong batch)."""
    return jsonify(_batch_mgr().stop_file(stem))
