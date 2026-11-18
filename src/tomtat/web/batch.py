"""Module web.batch — Re-export từ services.batch_service để đảm bảo tương thích ngược."""
from ..services.batch_service import (
    BatchManager,
    make_client,
    resolve_files,
)

__all__ = ["BatchManager", "make_client", "resolve_files"]
