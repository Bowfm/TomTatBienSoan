"""Quản lý trạng thái, bản nháp và lịch sử phiên bản của từng file trong work\\<stem>\\."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

STATUS_PENDING = "chua_xu_ly"
STATUS_PARTIAL = "dang_lam_do"
STATUS_DRAFT = "da_co_nhap"
STATUS_MANUAL = "dang_lam_do"  # Alias tương thích
STATUS_APPROVED = "da_duyet"
STATUS_EXPORTED = "da_xuat"
STATUS_ERROR = "loi"

STATUS_VI = {
    STATUS_PENDING: "chưa xử lý",
    STATUS_PARTIAL: "đang làm dở",
    STATUS_DRAFT: "đã có nháp",
    STATUS_APPROVED: "đã duyệt",
    STATUS_EXPORTED: "đã xuất",
    STATUS_ERROR: "lỗi",
    "can_xu_ly_tay": "đang làm dở",  # Tương thích file cũ trên ổ đĩa
}

DONE_STATUSES = {STATUS_DRAFT, STATUS_APPROVED, STATUS_EXPORTED}


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def write_json_atomic(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path) -> dict | None:
    path = Path(path)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


class FileWorkspace:
    """Thư mục làm việc work\\<stem>\\ của một file .docx."""

    def __init__(self, work_root: Path, docx_path: Path):
        self.docx_path = Path(docx_path)
        self.stem = self.docx_path.stem
        self.dir = Path(work_root) / self.stem
        self.state_path = self.dir / "state.json"
        self.units_path = self.dir / "units.json"
        self.drafts_dir = self.dir / "drafts"
        self.logs_dir = self.dir / "logs"

    def ensure_dirs(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def default_state(self) -> dict:
        return {
            "file": self.docx_path.name,
            "stem": self.stem,
            "source_path": str(self.docx_path),
            "status": STATUS_PENDING,
            "status_vi": STATUS_VI[STATUS_PENDING],
            "updated_at": now_iso(),
            "flags": [],
            "error_message": None,
            "stale_steps": [],
            "label": {
                "value": "",
                "per_doc": [],
                "confidence": "",
                "reason": "",
                "computed_majority": "",
            },
            "extract": {
                "selections": {},
                "text": "",
                "words": 0,
                "compress": 0.0,
                "note": "",
                "attempts": 0,
                "custom_edited": False,
            },
            "abstract": {
                "text": "",
                "words": 0,
                "note": "",
                "attempts": 0,
            },
            "judge": None,
            "checks": None,
            "current_version": 0,
            "output_path": None,
        }

    def load_state(self) -> dict:
        st = read_json(self.state_path)
        if st is None:
            return self.default_state()
        base = self.default_state()
        base.update(st)
        base["status_vi"] = STATUS_VI.get(base["status"], base["status"])
        return base

    def save_state(self, st: dict) -> dict:
        self.ensure_dirs()
        st["updated_at"] = now_iso()
        st["status_vi"] = STATUS_VI.get(st["status"], st["status"])
        write_json_atomic(self.state_path, st)
        return st

    def load_units(self) -> dict | None:
        return read_json(self.units_path)

    def save_units(self, units_data: dict) -> None:
        self.ensure_dirs()
        write_json_atomic(self.units_path, units_data)

    def save_draft_version(self, st: dict, source: str = "ai", note: str = "") -> int:
        """Lưu một bản chụp nháp vào drafts\\vNNN.json để có thể khôi phục."""
        self.ensure_dirs()
        existing = sorted(self.drafts_dir.glob("v*.json"))
        next_v = len(existing) + 1
        for p in existing:
            try:
                v = int(p.stem.lstrip("v"))
                if v >= next_v:
                    next_v = v + 1
            except ValueError:
                pass
        snap = {
            "version": next_v,
            "saved_at": now_iso(),
            "source": source,
            "note": note,
            "label": st.get("label", {}),
            "extract": {
                "selections": st.get("extract", {}).get("selections", {}),
                "text": st.get("extract", {}).get("text", ""),
                "words": st.get("extract", {}).get("words", 0),
                "custom_edited": st.get("extract", {}).get("custom_edited", False),
            },
            "abstract": {
                "text": st.get("abstract", {}).get("text", ""),
                "words": st.get("abstract", {}).get("words", 0),
            },
            "judge": st.get("judge"),
            "checks_summary": {
                "ok": (st.get("checks") or {}).get("ok"),
                "n_errors": (st.get("checks") or {}).get("n_errors", 0),
                "n_warnings": (st.get("checks") or {}).get("n_warnings", 0),
            },
        }
        write_json_atomic(self.drafts_dir / f"v{next_v:03d}.json", snap)
        st["current_version"] = next_v
        return next_v

    def list_draft_versions(self) -> list[dict]:
        if not self.drafts_dir.exists():
            return []
        out = []
        for p in sorted(self.drafts_dir.glob("v*.json"), reverse=True):
            d = read_json(p)
            if d:
                out.append(d)
        return out

    def get_draft_version(self, version: int) -> dict | None:
        return read_json(self.drafts_dir / f"v{int(version):03d}.json")


def discover_docx_files(target: Path) -> list[Path]:
    """Tìm các file .docx trong thư mục (hoặc trả về chính file đó), bỏ qua file tạm ~$."""
    target = Path(target)
    if target.is_file():
        return [target] if target.suffix.lower() == ".docx" and not target.name.startswith("~$") else []
    if not target.exists():
        return []
    files = [
        p for p in sorted(target.rglob("*.docx"))
        if not p.name.startswith("~$") and not p.name.endswith(".tmp.docx")
    ]
    return files
