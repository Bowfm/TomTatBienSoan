"""Blueprint: review — duyệt bài, lưu nháp, kiểm tra, ghép trích chọn, khôi phục."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from ...checks import run_checks
from ...docx_io import DocxError
from ...services.file_service import FileService
from ...textutil import nfc
from ...units import assemble_extract, compute_budget, normalize_selections

bp = Blueprint("review", __name__)


def _cfg():
    return current_app.config["TOMTAT_CFG"]


def _service() -> FileService:
    return FileService(_cfg())


@bp.post("/api/file/<stem>/check")
def api_check(stem: str):
    """Kiểm tra tức thì khi người dùng sửa nhãn / trích chọn / biên soạn."""
    svc = _service()
    files_map = svc.resolve_files()
    if stem not in files_map:
        return jsonify({"error": f"Không tìm thấy file '{stem}'."}), 404
    ws = svc.get_workspace(files_map[stem])
    units_data = ws.load_units()
    if not units_data:
        return jsonify({"error": "File chưa được inspect."}), 400
    if "total_words" in (units_data.get("budget") or {}):
        units_data["budget"] = compute_budget(units_data["budget"]["total_words"], svc.cfg)
    payload = request.get_json(force=True) or {}
    style = payload.get("style", "")
    extract = payload.get("extract", "")
    abstract = payload.get("abstract", "")
    chk = run_checks(units_data, style, extract, abstract, svc.cfg)
    return jsonify({"checks": chk.to_dict()})


@bp.post("/api/file/<stem>/assemble")
def api_assemble(stem: str):
    """Ghép bản trích chọn từ các ID đơn vị được tick trên giao diện."""
    svc = _service()
    files_map = svc.resolve_files()
    if stem not in files_map:
        return jsonify({"error": f"Không tìm thấy file '{stem}'."}), 404
    ws = svc.get_workspace(files_map[stem])
    units_data = ws.load_units()
    if not units_data:
        return jsonify({"error": "File chưa được inspect."}), 400
    if "total_words" in (units_data.get("budget") or {}):
        units_data["budget"] = compute_budget(units_data["budget"]["total_words"], svc.cfg)
    payload = request.get_json(force=True) or {}
    selections = payload.get("selections") or {}
    style = payload.get("style") or ""
    abstract = payload.get("abstract") or ""
    sel_clean, id_errors = normalize_selections(units_data, selections)
    text, per_doc_words = assemble_extract(units_data, sel_clean)
    chk = run_checks(units_data, style, text, abstract, svc.cfg)
    return jsonify({
        "extract": text,
        "selections": {str(k): v for k, v in sel_clean.items()},
        "per_doc_words": {str(k): v for k, v in per_doc_words.items()},
        "id_errors": id_errors,
        "checks": chk.to_dict(),
    })


@bp.post("/api/file/<stem>/save")
def api_save(stem: str):
    """Lưu bản nháp hiện tại và ghi thêm một phiên bản vào lịch sử."""
    svc = _service()
    payload = request.get_json(force=True) or {}
    try:
        res = svc.save_draft(
            stem=stem,
            style=payload.get("style", ""),
            extract=payload.get("extract", ""),
            abstract=payload.get("abstract", ""),
            selections=payload.get("selections"),
            note=payload.get("note", ""),
            custom_edited=bool(payload.get("custom_edited", False)),
        )
        return jsonify({
            "ok": True,
            "version": res["version"],
            "state": res["state"],
            "versions": res["versions"],
        })
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404


@bp.post("/api/file/<stem>/restore")
def api_restore(stem: str):
    """Khôi phục một phiên bản nháp trong lịch sử."""
    svc = _service()
    payload = request.get_json(force=True) or {}
    ver = int(payload.get("version", 0))
    try:
        res = svc.restore_draft(stem=stem, version=ver)
        return jsonify({
            "ok": True,
            "version": res["version"],
            "state": res["state"],
            "versions": res["versions"],
        })
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404


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
