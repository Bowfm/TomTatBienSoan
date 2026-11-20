"""Blueprint: system — cập nhật hệ thống, xóa file."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from ...services.file_service import FileService

bp = Blueprint("system", __name__)


def _cfg():
    return current_app.config["TOMTAT_CFG"]


def _service() -> FileService:
    return FileService(_cfg())


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
    """Trả về thông tin cập nhật của phiên khởi động hiện tại (hoặc kiểm tra trực tiếp nếu `?live=1`)."""
    from ..app import get_startup_update_info

    live = request.args.get("live", "").strip().lower() in ("1", "true", "yes")
    info = get_startup_update_info(current_app, live=live)
    return jsonify(info)


@bp.post("/api/self-update")
def api_self_update():
    """Kéo bản cập nhật code/prompt mới nhất từ GitHub."""
    import os
    import threading
    from ... import updater as updater_mod

    mgr = current_app.config.get("TOMTAT_BATCH_MGR")
    if mgr is not None:
        st = mgr.status()
        if (
            st.get("active")
            or st.get("running")
            or st.get("all_running")
            or st.get("rerun_running")
            or st.get("queued_rerun_stems")
        ):
            return jsonify({
                "ok": False,
                "output": "Đang có tiến trình AI chạy. Vui lòng đợi chạy xong hoặc bấm Dừng AI trước khi cập nhật.",
            }), 409

    cfg = _cfg()
    if getattr(updater_mod.run_update, "__module__", "") != updater_mod.__name__:
        ok_raw, msg_raw = updater_mod.run_update(cfg.root)
        details = {
            "ok": bool(ok_raw),
            "message": str(msg_raw),
            "updated": bool(ok_raw),
            "changed_python": False,
            "current_commit": updater_mod.get_current_commit(cfg.root) if ok_raw else "",
        }
    else:
        details = updater_mod.run_update_detailed(cfg.root)

    ok = bool(details.get("ok"))
    msg = str(details.get("message") or "")
    cur_short = ""
    restarting = False
    restart_required = False
    if ok:
        cfg.reload()
        cur = str(details.get("current_commit") or updater_mod.get_current_commit(cfg.root) or "")
        cur_short = cur[:7] if cur else ""
        new_info = {
            "has_update": False,
            "status": "up_to_date",
            "current_commit": cur_short,
            "latest_commit": cur_short,
            "message": (
                f"Đã là phiên bản mới nhất ({cur_short})."
                if cur_short
                else "Đã là phiên bản mới nhất."
            ),
        }
        lock = current_app.config.get("TOMTAT_STARTUP_UPDATE_LOCK")
        if lock is not None:
            with lock:
                current_app.config["TOMTAT_STARTUP_UPDATE_INFO"] = new_info
        else:
            current_app.config["TOMTAT_STARTUP_UPDATE_INFO"] = new_info

        changed_py = bool(details.get("changed_python"))
        can_auto_restart = (
            os.environ.get("TOMTAT_RESTART_ON_EXIT") == "1"
            and not current_app.testing
        )
        if changed_py and can_auto_restart:
            restarting = True
            t = threading.Timer(0.4, os._exit, args=(42,))
            t.daemon = True
            t.start()
        elif changed_py:
            restart_required = True

    return jsonify({
        "ok": ok,
        "output": msg.strip(),
        "current_commit": cur_short,
        "changed_python": bool(details.get("changed_python")),
        "restarting": restarting,
        "restart_required": restart_required,
    })
