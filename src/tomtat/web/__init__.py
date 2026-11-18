"""Giao diện web local duyệt và chỉnh sửa kết quả."""
from .app import create_app
from .batch import BatchManager

__all__ = ["create_app", "BatchManager"]
