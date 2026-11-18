"""Package web.routes — Blueprints chia nhỏ theo từng nhóm nghiệp vụ web."""
from __future__ import annotations

from flask import Flask

from .files import bp as files_bp
from .process import bp as process_bp
from .review import bp as review_bp
from .system import bp as system_bp


def register_routes(app: Flask) -> None:
    """Đăng ký tất cả các route blueprints vào Flask app."""
    app.register_blueprint(files_bp)
    app.register_blueprint(process_bp)
    app.register_blueprint(review_bp)
    app.register_blueprint(system_bp)


__all__ = ["register_routes", "files_bp", "process_bp", "review_bp", "system_bp"]
