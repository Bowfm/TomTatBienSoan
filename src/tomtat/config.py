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
                      "sentence_split", "judge", "workers", "max_workers",
                      "review", "layout", "label"):
                if k in fresh:
                    self[k] = fresh[k]
            if "max_workers" not in fresh:
                self.pop("max_workers", None)  # bỏ trần khi người dùng xóa dòng max_workers khỏi config.yaml
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


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = _deep_merge(merged[k], v)
        else:
            merged[k] = v
    return merged


def load_config(path: str | os.PathLike | None = None, root: str | os.PathLike | None = None) -> Config:
    root_p = Path(root) if root else PROJECT_ROOT
    cfg_path = Path(path) if path else root_p / "config.yaml"
    with open(cfg_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if path is None:
        local_cfg_path = root_p / "config.local.yaml"
        if local_cfg_path.exists():
            try:
                with open(local_cfg_path, encoding="utf-8-sig") as lf:
                    local_data = yaml.safe_load(lf) or {}
                if isinstance(local_data, dict):
                    data = _deep_merge(data, local_data)
            except Exception as exc:
                logger.warning("Không đọc được config.local.yaml (%s); bỏ qua.", exc)
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


def get_api_keys(root: Path | None = None) -> list[str]:
    """Lấy danh sách các API Key từ api_keys.txt (ưu tiên cao nhất) hoặc GEMINI_API_KEY/GEMINI_API_KEYS."""
    import re
    keys: list[str] = []

    base_root = Path(root) if root else PROJECT_ROOT
    # 1. Ưu tiên từ file api_keys.txt nếu có trong thư mục gốc
    kf = base_root / "api_keys.txt"
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


def get_api_key(root: Path | None = None) -> str | None:
    keys = get_api_keys() if root is None else get_api_keys(root=root)
    return keys[0] if keys else None


def get_pool_scaling(cfg: dict | None = None, api_keys: list[str] | None = None) -> dict:
    """Tự động tính toán các thông số phụ thuộc vào số lượng API key hiện có trong pool:
    - key_count: Tổng số API key hiện có
    - recommended_workers: Số luồng xử lý file song song tối ưu (tự động scale khi thêm/bớt key)
    - max_concurrent_calls: Số lượt gọi API đồng thời tối đa
    - concurrency_options: Các mức luồng gợi ý trên giao diện web
    """
    import tomtat.config as _cfg_mod

    if api_keys is None:
        api_keys = _cfg_mod.get_api_keys()
    key_count = max(1, len(api_keys))

    g = (cfg.get("gemini") or {}) if cfg else {}
    raw_workers = cfg.get("workers") if cfg else None
    raw_max_calls = g.get("max_concurrent_calls")
    raw_cap = cfg.get("max_workers") if cfg else None
    # Trần cứng số file xử lý song song (áp dụng cho UI, API /api/batch/start và CLI)
    worker_cap = raw_cap if isinstance(raw_cap, int) and not isinstance(raw_cap, bool) and raw_cap > 0 else None

    # Công thức tự động scale theo số lượng API keys:
    # Cứ ~10 keys (hoặc 1 project) tăng thêm 1 luồng xử lý song song an toàn cho các model 5 RPM.
    # Ví dụ: 1-14 keys -> 2 luồng; 15-29 keys -> 3 luồng; 30-44 keys -> 4 luồng;
    # 45-59 keys -> 5 luồng; 60-69 keys -> 6 luồng; 70-79 keys -> 7 luồng; 80+ keys -> 8..16 luồng.
    if key_count <= 2:
        auto_workers = 1
    elif key_count < 60:
        auto_workers = max(2, min(6, key_count // 12 + 1))
    else:
        auto_workers = min(16, max(4, key_count // 10))

    if isinstance(raw_workers, int) and not isinstance(raw_workers, bool) and raw_workers > 0:
        recommended_workers = raw_workers
    else:
        recommended_workers = auto_workers
    if worker_cap:
        recommended_workers = min(recommended_workers, worker_cap)

    auto_max_calls = max(recommended_workers + 2, min(24, max(4, (key_count // 10) + 2)))
    if isinstance(raw_max_calls, int) and raw_max_calls > 0:
        max_concurrent_calls = max(recommended_workers, raw_max_calls)
    else:
        max_concurrent_calls = auto_max_calls

    base_opts = {1, 2, 4, 6, 8, 10, 12, 14, 16}
    base_opts.add(recommended_workers)
    max_opt = max(12, min(24, recommended_workers + 6))
    if worker_cap:
        max_opt = worker_cap
    options = sorted(x for x in base_opts if 1 <= x <= max_opt)

    return {
        "key_count": len(api_keys),
        "recommended_workers": recommended_workers,
        "max_concurrent_calls": max_concurrent_calls,
        "concurrency_options": options,
        "max_workers": worker_cap,
    }


def clamp_workers(requested: int | None, cfg: dict | None = None, api_keys: list[str] | None = None) -> int:
    """Chuẩn hóa số luồng xử lý file: None/<=0 -> recommended_workers; luôn <= max_workers (nếu cấu hình)."""
    scaling = get_pool_scaling(cfg, api_keys=api_keys)
    try:
        n = int(requested) if requested is not None else 0
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        n = scaling["recommended_workers"]
    cap = scaling.get("max_workers")
    if cap:
        n = min(n, cap)
    return max(1, n)
