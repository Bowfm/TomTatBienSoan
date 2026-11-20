"""Flask server cho giao diện duyệt local (chỉ lắng nghe trên 127.0.0.1)."""
from __future__ import annotations

from pathlib import Path
import threading
import time

from flask import Flask, render_template, request

from .. import STYLES
from ..config import Config
from ..llm import get_rpd_status
from ..state import STATUS_VI
from ..textutil import configure as configure_textutil
from .batch import BatchManager, make_client, resolve_files
from .routes import register_routes

# Re-exports cho backward compatibility (tests và các module khác)
__all__ = ["create_app", "get_startup_update_info", "BatchManager", "make_client", "resolve_files"]


def get_startup_update_info(app: Flask, *, live: bool = False) -> dict:
    """Lấy kết quả kiểm tra cập nhật gắn với phiên khởi động hiện tại của server.

    - `live=False`: Chỉ kiểm tra GitHub một lần duy nhất cho phiên khởi động hiện tại và giữ
      nguyên kết quả đó suốt phiên làm việc. Nếu có bản cập nhật mới trong lúc người dùng
      đang sử dụng, thông báo chỉ hiển thị ở phiên khởi động tiếp theo.
    - `live=True`: Kiểm tra trực tiếp GitHub khi người dùng bấm nút cập nhật thủ công,
      không làm bật thông báo tự động giữa phiên.
    """
    if not live:
        cached = app.config.get("TOMTAT_STARTUP_UPDATE_INFO")
        if cached is not None:
            return dict(cached)

    lock = app.config.setdefault("TOMTAT_STARTUP_UPDATE_LOCK", threading.Lock())
    with lock:
        if not live:
            cached = app.config.get("TOMTAT_STARTUP_UPDATE_INFO")
            if cached is not None:
                return dict(cached)
        from ..updater import check_update, consume_startup_update_cache

        cfg = app.config["TOMTAT_CFG"]
        info = None
        if not live:
            info = consume_startup_update_cache(cfg.root)
        if info is None:
            info = check_update(cfg.root, include_details=True)
        if not live or app.config.get("TOMTAT_STARTUP_UPDATE_INFO") is None:
            app.config["TOMTAT_STARTUP_UPDATE_INFO"] = dict(info)
        return dict(info)


def create_app(cfg: Config, mock: bool = False) -> Flask:
    """Tạo Flask application instance với các route blueprints đã được modularize."""
    here = Path(__file__).parent
    app = Flask(
        __name__,
        template_folder=str(here / "templates"),
        static_folder=str(here / "static"),
    )
    app.config["JSON_AS_ASCII"] = False
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

    # Lưu context dùng chung trong app.config để các blueprints truy cập
    app.config["TOMTAT_CFG"] = cfg
    app.config["TOMTAT_MOCK"] = mock
    app.config["TOMTAT_BATCH_MGR"] = BatchManager(cfg)
    app.config["TOMTAT_STARTED_EPOCH"] = time.time()
    app.config["TOMTAT_STARTUP_UPDATE_LOCK"] = threading.Lock()
    app.config["TOMTAT_STARTUP_UPDATE_INFO"] = None

    sc = cfg.get("sentence_split", {})
    configure_textutil(sc.get("abbreviations"), sc.get("heading_words"))

    @app.after_request
    def _no_cache_static(resp):
        if "Cache-Control" not in resp.headers or request.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        return resp

    @app.get("/")
    def index():
        return render_template(
            "index.html",
            styles=STYLES,
            status_vi=STATUS_VI,
            mock=mock,
            rpd_status=get_rpd_status(cfg),
        )

    # Đăng ký các blueprints: files, process, review, system
    register_routes(app)

    return app
