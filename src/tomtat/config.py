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


def get_api_keys() -> list[str]:
    """Lấy danh sách các API Key từ GEMINI_API_KEY/GEMINI_API_KEYS (tách bằng phẩy/xuống dòng) hoặc api_keys.txt."""
    import re
    keys: list[str] = []

    # 1. Từ biến môi trường GEMINI_API_KEYS hoặc GEMINI_API_KEY (hỗ trợ phân tách bằng dấu phẩy, chấm phẩy hoặc xuống dòng)
    raw = os.environ.get("GEMINI_API_KEYS") or os.environ.get("GEMINI_API_KEY") or ""
    for part in re.split(r"[,;\n\r]+", raw):
        k = part.strip()
        if k and k not in keys:
            keys.append(k)

    # 2. Hoặc từ các biến GEMINI_API_KEY_1, GEMINI_API_KEY_2...
    for k_env, val in sorted(os.environ.items()):
        if k_env.startswith("GEMINI_API_KEY_") and val.strip():
            k = val.strip()
            if k not in keys:
                keys.append(k)

    # 3. Hoặc từ file api_keys.txt nếu có trong thư mục gốc
    for key_file in (Path("api_keys.txt"), Path(__file__).resolve().parent.parent.parent / "api_keys.txt"):
        if key_file.exists():
            try:
                for line in key_file.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and line not in keys:
                        keys.append(line)
            except Exception:
                pass
            break

    return keys


def get_api_key() -> str | None:
    keys = get_api_keys()
    return keys[0] if keys else None
