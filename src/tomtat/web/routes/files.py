"""Blueprint: files — quản lý file (danh sách, tải lên, đọc, logs)."""
from __future__ import annotations

import re
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from ... import STYLES
from ... import config as _cfg_mod
from ...config import get_pool_scaling, load_config
from ...docx_io import DocxError
from ...llm import get_rpd_status
from ...services.file_service import FileService
from ...state import STATUS_VI
from ...textutil import nfc
from ..batch import make_client

bp = Blueprint("files", __name__)


def _cfg():
    return current_app.config["TOMTAT_CFG"]


def _mock():
    return current_app.config.get("TOMTAT_MOCK", False)


def _service() -> FileService:
    return FileService(_cfg())


@bp.get("/api/files")
def api_files():
    cfg = _cfg()
    cfg.reload()
    fresh_cfg = load_config()
    svc = _service()
    rows = svc.get_status_rows()
    g = fresh_cfg.get("gemini", {})
    keys = _cfg_mod.get_api_keys()
    scaling = get_pool_scaling(cfg, api_keys=keys)
    running_stems: list[str] = []
    running_steps: dict[str, str] = {}
    stopping_stems: list[str] = []
    queued_rerun_stems: list[str] = []
    queued_rerun_steps: dict[str, str] = {}
    queued_rerun_positions: dict[str, int] = {}
    queued_reruns: list[dict] = []
    allowed_workers = scaling.get("recommended_workers", 1)
    mgr = current_app.config.get("TOMTAT_BATCH_MGR")
    if mgr is not None:
        bst = mgr.status()
        running_stems = bst.get("all_running", bst.get("running", []))
        running_steps = bst.get("all_running_steps", bst.get("running_steps", {}))
        stopping_stems = bst.get("stopping_stems", [])
        queued_rerun_stems = bst.get("queued_rerun_stems", [])
        queued_rerun_steps = bst.get("queued_rerun_steps", {})
        queued_rerun_positions = bst.get("queued_rerun_positions", {})
        queued_reruns = bst.get("queued_reruns", [])
        allowed_workers = bst.get("allowed_workers", allowed_workers)
    return jsonify({
        "files": rows,
        "styles": STYLES,
        "status_vi": STATUS_VI,
        "mock": _mock(),
        "has_api_key": bool(keys),
        "api_key_count": len(keys),
        "pool_scaling": scaling,
        "allowed_workers": allowed_workers,
        "primary_model": g.get("model", ""),
        "fallback_models": g.get("fallback_models") or [],
        "learned_count": svc.get_learned_count(),
        "rpd_status": get_rpd_status(cfg, api_keys=keys),
        "running_stems": running_stems,
        "running_steps": running_steps,
        "stopping_stems": stopping_stems,
        "queued_rerun_stems": queued_rerun_stems,
        "queued_rerun_steps": queued_rerun_steps,
        "queued_rerun_positions": queued_rerun_positions,
        "queued_reruns": queued_reruns,
    })


@bp.get("/api/rpd")
def api_rpd():
    """Trả về thống kê RPD (Requests Per Day) hiện tại của từng model."""
    cfg = _cfg()
    cfg.reload()
    keys = _cfg_mod.get_api_keys()
    return jsonify({
        "api_key_count": len(keys),
        "pool_scaling": get_pool_scaling(cfg, api_keys=keys),
        "rpd_status": get_rpd_status(cfg, api_keys=keys),
    })


@bp.post("/api/upload")
def api_upload():
    """Tải một hoặc nhiều file .docx từ trình duyệt vào thư mục input\\ (và tùy chọn chạy AI ngay)."""
    cfg = _cfg()
    svc = _service()
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
        try:
            svc.inspect_file(dst, force=True, reset=overwrite)
            processed_stems.append(dst.stem)
        except DocxError as exc:
            errors.append(f"{dst.name}: {exc}")
            continue

    processed_stems.sort()

    if run_now and processed_stems:
        try:
            client = make_client(cfg, force_mock=use_mock)
            for stem in processed_stems:
                dst = cfg.input_dir / f"{stem}.docx"
                res = svc.process(dst, client, force=True)
                if res.get("error"):
                    errors.append(f"{dst.name}: {res['error']}")
        except RuntimeError as exc:
            errors.append(str(exc))

    svc.write_reports()
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
    cfg.reload()
    svc = _service()
    try:
        detail = svc.get_file_detail(stem)
        detail["rpd_status"] = get_rpd_status(cfg)
        return jsonify(detail)
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except DocxError as exc:
        return jsonify({"error": str(exc)}), 400


@bp.get("/api/file/<stem>/logs")
def api_get_logs(stem: str):
    """Trả về danh sách các lần gọi Gemini (logs) của file để xem trực tiếp trên web."""
    cfg = _cfg()
    cfg.reload()
    svc = _service()
    running_stems: list[str] = []
    running_steps: dict[str, str] = {}
    stopping_stems: list[str] = []
    queued_rerun_stems: list[str] = []
    queued_rerun_steps: dict[str, str] = {}
    queued_rerun_positions: dict[str, int] = {}
    allowed_workers = 0
    batch_active = False
    mgr = current_app.config.get("TOMTAT_BATCH_MGR")
    if mgr is not None:
        bst = mgr.status()
        batch_active = bst.get("active", False)
        running_stems = bst.get("all_running", bst.get("running", []))
        running_steps = bst.get("all_running_steps", bst.get("running_steps", {}))
        stopping_stems = bst.get("stopping_stems", [])
        queued_rerun_stems = bst.get("queued_rerun_stems", [])
        queued_rerun_steps = bst.get("queued_rerun_steps", {})
        queued_rerun_positions = bst.get("queued_rerun_positions", {})
        allowed_workers = bst.get("allowed_workers", 0)
    res = svc.get_file_logs(
        stem,
        is_running=(stem in running_stems),
        server_started_epoch=current_app.config.get("TOMTAT_STARTED_EPOCH"),
    )
    res["rpd_status"] = get_rpd_status(cfg)
    res["batch_active"] = batch_active
    res["running_stems"] = running_stems
    res["running_steps"] = running_steps
    res["stopping_stems"] = stopping_stems
    res["queued_rerun_stems"] = queued_rerun_stems
    res["queued_rerun_steps"] = queued_rerun_steps
    res["queued_rerun_positions"] = queued_rerun_positions
    res["allowed_workers"] = allowed_workers
    return jsonify(res)
