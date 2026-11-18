"""Nạp cấu hình (config.yaml) và biến môi trường (.env)."""
from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml

PROJECT_ROOT = Path(os.environ.get("TOMTAT_HOME") or Path(__file__).resolve().parents[2])
logger = logging.getLogger("tomtat.config")


class ConfigValidationError(ValueError):
    """Lỗi khi file config.yaml thiếu các trường bắt buộc."""


class Config(dict):
    """dict có thêm vài tiện ích về đường dẫn và kiểm tra tính hợp lệ (schema validation)."""

    root: Path

    def path(self, key: str) -> Path:
        p = Path(self["paths"][key])
        return p if p.is_absolute() else (self.root / p)

    @property
    def input_dir(self) -> Path:
        return self.path("input")

    @property
    def output_dir(self) -> Path:
        return self.path("output")

    @property
    def work_dir(self) -> Path:
        return self.path("work")

    @property
    def prompts_dir(self) -> Path:
        return self.path("prompts")

    def reload(self) -> None:
        """Nạp lại các mục config non-path từ disk (extract, abstract, grading, gemini, ...)."""
        try:
            fresh = load_config(root=getattr(self, "root", None))
            for k in ("extract", "abstract", "grading", "gemini", "word_count",
                      "sentence_split", "judge", "workers"):
                if k in fresh:
                    self[k] = fresh[k]
        except Exception:
            pass

    def validate(self, raise_on_error: bool = False) -> list[str]:
        """Kiểm tra tính toàn vẹn của cấu hình hệ thống."""
        errors: list[str] = []
        if "paths" not in self:
            errors.append("Thiếu mục 'paths' trong cấu hình.")
        else:
            for req in ("input", "output", "work", "prompts"):
                if req not in self["paths"]:
                    errors.append(f"Thiếu đường dẫn paths.{req} trong cấu hình.")

        if "gemini" not in self:
            errors.append("Thiếu mục 'gemini' trong cấu hình.")
        elif not self["gemini"].get("model"):
            errors.append("Thiếu tên model chính tại gemini.model.")

        if "extract" not in self:
            errors.append("Thiếu mục 'extract' trong cấu hình.")

        if "abstract" not in self:
            errors.append("Thiếu mục 'abstract' trong cấu hình.")

        if "grading" not in self:
            errors.append("Thiếu mục 'grading' trong cấu hình.")

        if errors and raise_on_error:
            raise ConfigValidationError("Cấu hình không hợp lệ: " + "; ".join(errors))

        return errors


def load_config(path: str | os.PathLike | None = None, root: str | os.PathLike | None = None) -> Config:
    root_p = Path(root) if root else PROJECT_ROOT
    cfg_path = Path(path) if path else root_p / "config.yaml"
    with open(cfg_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    cfg = Config(data)
    cfg.root = root_p
    _load_dotenv(root_p)
    return cfg


def _load_dotenv(root: Path) -> None:
    env = root / ".env"
    if env.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env, override=True, encoding="utf-8")
        except ImportError:  # pragma: no cover
            pass


def get_api_keys() -> list[str]:
    """Lấy danh sách các API Key từ api_keys.txt (ưu tiên cao nhất) hoặc GEMINI_API_KEY/GEMINI_API_KEYS."""
    import re
    keys: list[str] = []

    # 1. Ưu tiên từ file api_keys.txt nếu có trong thư mục gốc
    kf = PROJECT_ROOT / "api_keys.txt"
    if kf.exists():
        try:
            for line in kf.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and line not in keys:
                    keys.append(line)
        except Exception:
            pass

    # 2. Nếu chưa có từ api_keys.txt, lấy từ biến môi trường GEMINI_API_KEYS hoặc GEMINI_API_KEY
    if not keys:
        raw = os.environ.get("GEMINI_API_KEYS") or os.environ.get("GEMINI_API_KEY") or ""
        for part in re.split(r"[,;\n\r]+", raw):
            k = part.strip()
            if k and k not in keys:
                keys.append(k)

        for k_env, val in sorted(os.environ.items()):
            if k_env.startswith("GEMINI_API_KEY_") and val.strip():
                k = val.strip()
                if k not in keys:
                    keys.append(k)

    return keys


def get_api_key() -> str | None:
    keys = get_api_keys()
    return keys[0] if keys else None
