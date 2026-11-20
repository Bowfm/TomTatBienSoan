"""Quản lý trạng thái, bản nháp và lịch sử phiên bản của từng file trong work\\<stem>\\."""
from __future__ import annotations

import copy
from datetime import datetime
import json
import os
from pathlib import Path
import re
import threading
import time

from .models import AbstractStep, ExtractStep, FileState, JudgeStep, LabelStep, UnitsData
from .textutil import nfc

STATUS_PENDING = "chua_xu_ly"
STATUS_PARTIAL = "chua_xu_ly"  # Alias tương thích ngược
STATUS_DRAFT = "da_co_nhap"
STATUS_MANUAL = "chua_xu_ly"   # Alias tương thích ngược
STATUS_APPROVED = "da_duyet"
STATUS_EXPORTED = "da_xuat"
STATUS_ERROR = "loi"

STATUS_VI = {
    STATUS_PENDING: "chưa xử lý",
    STATUS_DRAFT: "đã có nháp",
    STATUS_APPROVED: "đã duyệt",
    STATUS_EXPORTED: "đã xuất",
    STATUS_ERROR: "lỗi",
    "dang_lam_do": "chưa xử lý",      # Tương thích file cũ trên ổ đĩa
    "can_xu_ly_tay": "chưa xử lý",    # Tương thích file cũ trên ổ đĩa
}

DONE_STATUSES = {STATUS_DRAFT, STATUS_APPROVED, STATUS_EXPORTED}


_STATE_IO_LOCK = threading.RLock()


def now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def write_json_atomic(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    tmp = path.with_name(f".{path.name}.{threading.get_ident()}.tmp")
    with _STATE_IO_LOCK:
        tmp.write_text(payload, encoding="utf-8")
        for attempt in range(6):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 5:
                    try:
                        path.write_text(payload, encoding="utf-8")
                    finally:
                        tmp.unlink(missing_ok=True)
                    return
                time.sleep(0.01 * (attempt + 1))


def read_json(path: Path) -> dict | None:
    path = Path(path)
    with _STATE_IO_LOCK:
        if not path.exists():
            return None
        for attempt in range(4):
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (PermissionError, json.JSONDecodeError):
                if attempt == 3:
                    return None
                time.sleep(0.01 * (attempt + 1))
    return None


_VERSION_FILE_RE = re.compile(r"^v(\d+)$")


def _version_num_from_path(path: Path) -> int | None:
    m = _VERSION_FILE_RE.match(path.stem)
    return int(m.group(1)) if m else None


def _normalize_selections_sig(selections: dict | None) -> tuple[tuple[str, tuple[int, ...]], ...]:
    if not isinstance(selections, dict):
        return ()
    items: list[tuple[str, tuple[int, ...]]] = []
    for k, vals in selections.items():
        clean: list[int] = []
        for v in (vals or []):
            try:
                clean.append(int(v))
            except (TypeError, ValueError):
                pass
        if clean:
            items.append((str(k), tuple(sorted(set(clean)))))
    items.sort(key=lambda pair: pair[0])
    return tuple(items)


def has_draft_content(data: dict | None) -> bool:
    """Kiểm tra state/snapshot có chứa nội dung biên tập thực tế hay chưa."""
    if not isinstance(data, dict):
        return False
    lb = data.get("label") or {}
    ex = data.get("extract") or {}
    ab = data.get("abstract") or {}
    return bool(
        nfc(str(lb.get("value") or "")).strip()
        or nfc(str(ex.get("text") or "")).strip()
        or nfc(str(ab.get("text") or "")).strip()
        or _normalize_selections_sig(ex.get("selections"))
        or data.get("judge") is not None
    )


def version_content_signature(data: dict | None) -> tuple:
    """Chữ ký nội dung chuẩn hóa để so sánh 2 phiên bản (label, selections, extract, abstract, judge)."""
    if not isinstance(data, dict):
        return ("", (), "", "", None)
    lb = data.get("label") or {}
    ex = data.get("extract") or {}
    ab = data.get("abstract") or {}
    judge = data.get("judge")
    judge_sig = json.dumps(judge, sort_keys=True, ensure_ascii=False) if isinstance(judge, dict) else None
    return (
        nfc(str(lb.get("value") or "")).strip(),
        _normalize_selections_sig(ex.get("selections")),
        nfc(str(ex.get("text") or "")).strip(),
        nfc(str(ab.get("text") or "")).strip(),
        judge_sig,
    )


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

    def default_state(self) -> FileState:
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
            "stale_reason": None,
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

    def load_state(self) -> FileState:
        st = read_json(self.state_path)
        if st is None:
            return self.default_state()
        base = self.default_state()
        base.update(st)
        base["status_vi"] = STATUS_VI.get(base["status"], base["status"])
        return base

    def save_state(self, st: FileState) -> FileState:
        self.ensure_dirs()
        st["updated_at"] = now_iso()
        st["status_vi"] = STATUS_VI.get(st["status"], st["status"])
        write_json_atomic(self.state_path, st)
        return st

    def load_units(self) -> UnitsData | None:
        return read_json(self.units_path)

    def save_units(self, units_data: UnitsData) -> None:
        self.ensure_dirs()
        write_json_atomic(self.units_path, units_data)

    def _iter_version_files(self) -> list[tuple[int, Path]]:
        if not self.drafts_dir.exists():
            return []
        found: list[tuple[int, Path]] = []
        for p in self.drafts_dir.glob("v*.json"):
            v_num = _version_num_from_path(p)
            if v_num is not None:
                found.append((v_num, p))
        found.sort(key=lambda pair: pair[0], reverse=True)
        return found

    @staticmethod
    def _build_checks_summary(chk: dict | None) -> dict:
        chk = chk or {}
        metrics = chk.get("metrics") or {}
        return {
            "ok": chk.get("ok"),
            "n_errors": chk.get("n_errors", 0),
            "n_warnings": chk.get("n_warnings", 0),
            "ratio": metrics.get("ratio"),
            "compress": metrics.get("compress"),
        }

    def save_draft_version(
        self,
        st: FileState,
        source: str = "ai",
        note: str = "",
        *,
        restored_from: int | None = None,
        approved: bool = False,
    ) -> int:
        """Lưu một bản chụp nháp vào drafts\\vNNN.json để có thể khôi phục."""
        self.ensure_dirs()
        with _STATE_IO_LOCK:
            existing = self._iter_version_files()
            next_v = (existing[0][0] + 1) if existing else 1
            lb = st.get("label") or {}
            ext = st.get("extract") or {}
            ab = st.get("abstract") or {}
            snap: dict = {
                "version": next_v,
                "saved_at": now_iso(),
                "source": source,
                "note": nfc(str(note or "")).strip(),
                "status": st.get("status", STATUS_PENDING),
                "model_used": st.get("model_used"),
                "flags": copy.deepcopy(st.get("flags") or []),
                "stale_steps": copy.deepcopy(st.get("stale_steps") or []),
                "stale_reason": st.get("stale_reason"),
                "label": copy.deepcopy(lb),
                "extract": {
                    "selections": copy.deepcopy(ext.get("selections") or {}),
                    "per_doc_words": copy.deepcopy(ext.get("per_doc_words") or {}),
                    "text": ext.get("text", ""),
                    "words": ext.get("words", 0),
                    "compress": ext.get("compress", 0.0),
                    "rouge2_est": ext.get("rouge2_est"),
                    "note": ext.get("note", ""),
                    "attempts": ext.get("attempts", 0),
                    "custom_edited": bool(ext.get("custom_edited", False)),
                    "model_used": ext.get("model_used"),
                },
                "abstract": {
                    "text": ab.get("text", ""),
                    "words": ab.get("words", 0),
                    "note": ab.get("note", ""),
                    "attempts": ab.get("attempts", 0),
                    "model_used": ab.get("model_used"),
                },
                "judge": copy.deepcopy(st.get("judge")),
                "checks_summary": self._build_checks_summary(st.get("checks")),
            }
            if restored_from is not None:
                snap["restored_from"] = int(restored_from)
            if approved:
                snap["approved"] = True
                snap["approved_at"] = snap["saved_at"]
            write_json_atomic(self.drafts_dir / f"v{next_v:03d}.json", snap)
            st["current_version"] = next_v
            return next_v

    def mark_version_approved(
        self,
        version: int,
        *,
        status: str = STATUS_EXPORTED,
        checks: dict | None = None,
    ) -> dict | None:
        """Gắn cờ đã duyệt ngay trên version hiện tại (không đẻ thêm version trùng lặp)."""
        with _STATE_IO_LOCK:
            path = self.drafts_dir / f"v{int(version):03d}.json"
            snap = read_json(path)
            if not snap:
                return None
            snap["approved"] = True
            snap["approved_at"] = now_iso()
            snap["status"] = status
            if checks is not None:
                snap["checks_summary"] = self._build_checks_summary(checks)
            write_json_atomic(path, snap)
            return snap

    def list_draft_versions(self) -> list[dict]:
        items: list[tuple[int, dict]] = []
        for v_num, p in self._iter_version_files():
            d = read_json(p)
            if d:
                items.append((int(d.get("version") or v_num), d))
        items.sort(key=lambda pair: pair[0], reverse=True)
        return [d for _, d in items]

    def get_draft_version(self, version: int) -> dict | None:
        try:
            v = int(version)
        except (TypeError, ValueError):
            return None
        if v <= 0:
            return None
        return read_json(self.drafts_dir / f"v{v:03d}.json")

    def latest_draft_version(self) -> dict | None:
        for _, p in self._iter_version_files():
            d = read_json(p)
            if d:
                return d
        return None

    def differs_from_latest_version(self, st: dict) -> bool:
        """Trả về True nếu state hiện có nội dung biên tập khác với version lưu gần nhất."""
        if not has_draft_content(st):
            return False
        latest = self.latest_draft_version()
        if latest is None:
            return True
        return version_content_signature(st) != version_content_signature(latest)

    def find_previous_version(self, st: dict) -> dict | None:
        """Tìm version đích phù hợp nhất khi người dùng bấm 'Rollback về version trước'."""
        versions = self.list_draft_versions()
        if not versions:
            return None
        cur_sig = version_content_signature(st)
        # Nếu state hiện tại khác bản lưu gần nhất (vd: sửa tay chưa lưu version,
        # hoặc chạy lại AI bị lỗi giữa chừng sau khi restart=True xóa nháp) -> khôi phục về bản lưu gần nhất
        if cur_sig != version_content_signature(versions[0]):
            return versions[0]
        cur_v = int(st.get("current_version") or 0)
        older = [v for v in versions if int(v.get("version") or 0) < cur_v] if cur_v > 0 else versions[1:]
        if not older:
            older = versions[1:]
        if not older:
            return None
        for v in older:
            if version_content_signature(v) != cur_sig:
                return v
        return older[0]


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
