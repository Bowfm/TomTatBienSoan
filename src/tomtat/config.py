"""Nạp cấu hình (config.yaml) và biến môi trường (.env)."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

PROJECT_ROOT = Path(os.environ.get("TOMTAT_HOME") or Path(__file__).resolve().parents[2])


class Config(dict):
    """dict có thêm vài tiện ích về đường dẫn."""

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


def get_api_key() -> str | None:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    return key or None
