"""Blueprint: system — cập nhật hệ thống, tải file đơn/ZIP, xóa file."""
from __future__ import annotations

import io
from flask import Blueprint, current_app, jsonify, request, send_file

from ...services.file_service import FileService

bp = Blueprint("system", __name__)


def _cfg():
    return current_app.config["TOMTAT_CFG"]


def _service() -> FileService:
    return FileService(_cfg())


@bp.get("/api/download-file/<stem>")
def api_download_single(stem: str):
    """Tải lẻ một file .docx đã hoàn thành về trình duyệt."""
    cfg = _cfg()
    out_path = cfg.output_dir / f"{stem}.docx"
    if not out_path.exists():
        return jsonify({"error": f"Chưa có file xuất '{out_path.name}' trong output\\."}), 404
    return send_file(
        str(out_path),
        as_attachment=True,
        download_name=out_path.name,
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@bp.route("/api/download-zip", methods=["GET", "POST"])
def api_download_zip():
    """Đóng gói các file .docx đã xong thành file ZIP tên theo ngày hôm nay (VD: 17-11.zip)."""
    stems_req: list[str] = []
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        stems_req = [str(s).replace(".docx", "") for s in (payload.get("stems") or [])]
    else:
        raw = request.args.get("stems", "").strip()
        if raw:
            stems_req = [s.strip().replace(".docx", "") for s in raw.split(",") if s.strip()]

    svc = _service()
    try:
        zip_name, zip_bytes = svc.create_zip_export(stems=stems_req or None)
        buf = io.BytesIO(zip_bytes)
        return send_file(
            buf,
            as_attachment=True,
            download_name=zip_name,
            mimetype="application/zip",
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@bp.post("/api/delete-files")
def api_delete_files():
    """Xóa một hoặc nhiều file (hoặc toàn bộ batch trong ngày) khỏi input\\, work\\ và output\\."""
    payload = request.get_json(force=True) or {}
    mode = payload.get("mode", "selected")  # selected | pending | approved | all
    req_stems = [str(s).replace(".docx", "") for s in (payload.get("stems") or [])]

    svc = _service()
    deleted = svc.delete_files(stems=req_stems, mode=mode)
    return jsonify({
        "ok": True,
        "deleted": deleted,
        "count": len(deleted),
    })


@bp.get("/api/check-update")
def api_check_update():
    """Kiểm tra xem có bản cập nhật mới trên GitHub hay không."""
    from ...updater import check_update

    cfg = _cfg()
    info = check_update(cfg.root)
    return jsonify(info)


@bp.post("/api/self-update")
def api_self_update():
    """Kéo bản cập nhật code/prompt mới nhất từ GitHub."""
    from ...updater import run_update

    cfg = _cfg()
    ok, msg = run_update(cfg.root)
    return jsonify({
        "ok": ok,
        "output": msg.strip(),
    })
