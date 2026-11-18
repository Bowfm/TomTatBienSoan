"""Flask server cho giao diện duyệt local (chỉ lắng nghe trên 127.0.0.1)."""
from __future__ import annotations

from pathlib import Path

from flask import Flask, render_template

from .. import STYLES
from ..config import Config
from ..state import STATUS_VI
from ..textutil import configure as configure_textutil
from .batch import BatchManager, make_client, resolve_files
from .routes import register_routes

# Re-exports cho backward compatibility (tests và các module khác)
__all__ = ["create_app", "BatchManager", "make_client", "resolve_files"]

_resolve_files = resolve_files
_make_client = make_client


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

    sc = cfg.get("sentence_split", {})
    configure_textutil(sc.get("abbreviations"), sc.get("heading_words"))

    @app.get("/")
    def index():
        return render_template("index.html", styles=STYLES, status_vi=STATUS_VI, mock=mock)

    # Đăng ký các blueprints: files, process, review, system
    register_routes(app)

    return app
