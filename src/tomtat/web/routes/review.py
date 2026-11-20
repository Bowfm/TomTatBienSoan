"""Blueprint: review — duyệt bài, lưu nháp, kiểm tra, ghép trích chọn, khôi phục."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from ...docx_io import DocxError
from ...services.file_service import FileService
from ...textutil import nfc

bp = Blueprint("review", __name__)


def _cfg():
    return current_app.config["TOMTAT_CFG"]


def _service() -> FileService:
    return FileService(_cfg())


def _check_not_running(stem: str) -> str | None:
    mgr = current_app.config.get("TOMTAT_BATCH_MGR")
    if mgr is None:
        return None
    bst = mgr.status()
    running = bst.get("all_running", bst.get("running", []))
    if stem in running:
        return f"File '{stem}.docx' đang được AI xử lý, vui lòng đợi hoàn tất."
    return None


@bp.post("/api/file/<stem>/check")
def api_check(stem: str):
    """Kiểm tra tức thì khi người dùng sửa nhãn / trích chọn / biên soạn."""
    svc = _service()
    payload = request.get_json(force=True) or {}
    try:
        res = svc.check_content(
            stem=stem,
            style=payload.get("style", ""),
            extract=payload.get("extract", ""),
            abstract=payload.get("abstract", ""),
        )
        return jsonify(res)
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@bp.post("/api/file/<stem>/assemble")
def api_assemble(stem: str):
    """Ghép bản trích chọn từ các ID đơn vị được tick trên giao diện."""
    svc = _service()
    payload = request.get_json(force=True) or {}
    try:
        res = svc.assemble_extract_from_selections(
            stem=stem,
            selections=payload.get("selections") or {},
            style=payload.get("style") or "",
            abstract=payload.get("abstract") or "",
        )
        return jsonify(res)
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@bp.get("/api/file/<stem>/versions")
def api_versions(stem: str):
    """Lấy lịch sử phiên bản của một file."""
    svc = _service()
    try:
        return jsonify(svc.get_file_versions(stem))
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404


@bp.post("/api/file/<stem>/save")
def api_save(stem: str):
    """Lưu bản nháp hiện tại và (tùy chọn) ghi thêm một phiên bản vào lịch sử."""
    err_running = _check_not_running(stem)
    if err_running:
        return jsonify({"error": err_running, "already_running": True}), 409

    svc = _service()
    payload = request.get_json(force=True) or {}
    auto_save = bool(payload.get("auto_save", False))
    create_version = (
        bool(payload["create_version"])
        if "create_version" in payload
        else (not auto_save)
    )
    try:
        res = svc.save_draft(
            stem=stem,
            style=payload.get("style", ""),
            extract=payload.get("extract", ""),
            abstract=payload.get("abstract", ""),
            selections=payload.get("selections"),
            note=payload.get("note", ""),
            custom_edited=bool(payload.get("custom_edited", False)),
            create_version=create_version,
            dismiss_stale=bool(payload.get("dismiss_stale", False)),
        )
        return jsonify({
            "ok": True,
            "version": res["version"],
            "saved_new_version": res["saved_new_version"],
            "has_unsaved_changes": res["has_unsaved_changes"],
            "previous_version": res["previous_version"],
            "state": res["state"],
            "versions": res["versions"],
            "checks": res["checks"],
        })
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404


@bp.post("/api/file/<stem>/restore")
def api_restore(stem: str):
    """Khôi phục một phiên bản nháp trong lịch sử (hoặc rollback về version trước)."""
    err_running = _check_not_running(stem)
    if err_running:
        return jsonify({"error": err_running, "already_running": True}), 409

    svc = _service()
    payload = request.get_json(force=True) or {}
    raw_ver = payload.get("version")
    if raw_ver in (None, "", "previous"):
        ver = None
    else:
        try:
            ver = int(raw_ver)
        except (TypeError, ValueError):
            return jsonify({"error": f"Số phiên bản không hợp lệ: {raw_ver!r}"}), 400
        if ver <= 0:
            return jsonify({"error": "Số phiên bản phải >= 1."}), 400

    current_draft = None
    if any(k in payload for k in ("style", "extract", "abstract", "selections")):
        current_draft = {
            k: payload[k]
            for k in ("style", "extract", "abstract", "selections", "custom_edited")
            if k in payload
        }

    try:
        res = svc.restore_draft(stem=stem, version=ver, current_draft=current_draft)
        return jsonify({
            "ok": True,
            "version": res["version"],
            "restored_from": res["restored_from"],
            "has_unsaved_changes": res["has_unsaved_changes"],
            "previous_version": res["previous_version"],
            "state": res["state"],
            "versions": res["versions"],
        })
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400 if ver is None else 404


@bp.post("/api/file/<stem>/approve")
def api_approve(stem: str):
    """Duyệt và ghi ra output\\<file>.docx (chặn nếu còn LỖI)."""
    svc = _service()
    payload = request.get_json(force=True) or {}
    files_map = svc.resolve_files()
    if stem not in files_map:
        return jsonify({"error": f"Không tìm thấy file '{stem}'."}), 404
    ws = svc.get_workspace(files_map[stem])
    st = ws.load_state()

    style = nfc(payload.get("style") or st["label"]["value"]).strip()
    extract = nfc(payload.get("extract") if payload.get("extract") is not None else st["extract"]["text"])
    abstract = nfc(payload.get("abstract") if payload.get("abstract") is not None else st["abstract"]["text"])
    confirmed_warnings = bool(payload.get("confirm_warnings", False))
    allow_overwrite = bool(payload.get("allow_overwrite_content", True))

    try:
        res = svc.approve_and_export(
            stem=stem,
            style=style,
            extract=extract,
            abstract=abstract,
            confirmed_warnings=confirmed_warnings,
            allow_overwrite=allow_overwrite,
        )
        if not res.get("ok"):
            status_code = 400 if res.get("error") else 200
            return jsonify(res), status_code
        return jsonify(res)
    except DocxError as exc:
        return jsonify({"error": str(exc)}), 400
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
