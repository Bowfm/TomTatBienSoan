"""FileService — Tầng nghiệp vụ xử lý, duyệt, lưu trữ và xuất file tài liệu."""
from __future__ import annotations

import copy
from pathlib import Path
import shutil
import threading

from ..checks import run_checks
from ..config import Config
from ..docx_io import DocxError, fill_docx
from ..llm import DEFAULT_TIMEOUT_S, get_model_timeout_s
from ..memory import load_memory, record_approved_sample
from ..models import FileState, UnitsData
from ..pipeline import Pipeline, _mark_stale_after, finalize_status, process_file, step_inspect, sync_manual_label_metadata
from ..report import _infer_model_from_logs, collect_rows, infer_step_models_from_logs, write_reports
from ..state import (
    STATUS_APPROVED,
    STATUS_DRAFT,
    STATUS_EXPORTED,
    STATUS_PENDING,
    FileWorkspace,
    _normalize_selections_sig,
    discover_docx_files,
    read_json,
)
from ..textutil import count_words, nfc
from ..units import assemble_extract, compute_budget, normalize_selections
from .batch_service import resolve_files


class FileService:
    """Service điều phối nghiệp vụ file docx dùng chung cho cả Web và CLI."""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def resolve_files(self) -> dict[str, Path]:
        """Lấy bản đồ {stem: docx_path} cho tất cả các file trong hệ thống."""
        return resolve_files(self.cfg)

    def discover_files(self, target_dir: Path | None = None) -> list[Path]:
        """Tìm các file .docx trong thư mục chỉ định hoặc input_dir."""
        target = target_dir or self.cfg.input_dir
        return discover_docx_files(target)

    def get_workspace(self, docx_path_or_stem: str | Path) -> FileWorkspace:
        """Lấy FileWorkspace cho một file hoặc stem."""
        if isinstance(docx_path_or_stem, Path):
            return FileWorkspace(self.cfg.work_dir, docx_path_or_stem)
        files = self.resolve_files()
        path = files.get(docx_path_or_stem) or (self.cfg.input_dir / f"{docx_path_or_stem}.docx")
        return FileWorkspace(self.cfg.work_dir, path)

    def inspect_file(
        self, docx_path: Path, force: bool = False, reset: bool = False
    ) -> tuple[FileWorkspace, FileState, UnitsData | None]:
        """Kiểm tra và bóc tách các câu/đơn vị từ file .docx.

        - force: luôn chạy lại bước inspect.
        - reset: bỏ state cũ, bắt đầu từ state mặc định (dùng khi upload ghi đè).
        """
        ws = FileWorkspace(self.cfg.work_dir, docx_path)
        st = ws.default_state() if reset else ws.load_state()
        units = None if (force or reset) else ws.load_units()
        if units is None and docx_path.exists():
            units = step_inspect(ws, self.cfg, st)
        if units and "total_words" in (units.get("budget") or {}):
            units["budget"] = compute_budget(units["budget"]["total_words"], self.cfg)
        return ws, st, units

    def process(
        self,
        docx_path: Path,
        client,
        force: bool = False,
        only_step: str | None = None,
        ignore_mismatch: bool = False,
        restart: bool = False,
        cancel_event: threading.Event | None = None,
    ) -> dict:
        """Chạy pipeline AI cho một file docx."""
        pipeline = Pipeline(self.cfg, client)
        return pipeline.process(
            docx_path,
            force=force,
            only_step=only_step,
            ignore_mismatch=ignore_mismatch,
            restart=restart,
            cancel_event=cancel_event,
        )

    async def process_async(
        self,
        docx_path: Path,
        client,
        force: bool = False,
        only_step: str | None = None,
        ignore_mismatch: bool = False,
        restart: bool = False,
        cancel_event: threading.Event | None = None,
    ) -> dict:
        """Chạy pipeline AI bất đồng bộ (asyncio) cho một file docx."""
        pipeline = Pipeline(self.cfg, client)
        return await pipeline.process_async(
            docx_path,
            force=force,
            only_step=only_step,
            ignore_mismatch=ignore_mismatch,
            restart=restart,
            cancel_event=cancel_event,
        )

    @staticmethod
    def _apply_draft_fields(
        st: FileState,
        units_data: UnitsData | None,
        style: str,
        extract: str,
        abstract: str,
        selections: dict | None = None,
        custom_edited: bool | None = None,
        cfg: dict | None = None,
    ) -> None:
        style = nfc(style).strip()
        extract = nfc(extract)
        abstract = nfc(abstract)
        sync_manual_label_metadata(st, style, cfg)
        ext = st.setdefault("extract", {})
        if selections is not None:
            ext["selections"] = selections
        total = (units_data.get("budget") or {}).get("total_words", 0) if units_data else 0
        n_ext = count_words(extract)
        ext["text"] = extract
        ext["words"] = n_ext
        ext["compress"] = round(n_ext / total, 4) if total else 0.0
        if custom_edited is not None:
            ext["custom_edited"] = bool(custom_edited)
        ab = st.setdefault("abstract", {})
        ab["text"] = abstract
        ab["words"] = count_words(abstract)

    def save_draft(
        self,
        stem: str,
        style: str,
        extract: str,
        abstract: str,
        selections: dict | None = None,
        note: str = "",
        custom_edited: bool = False,
        *,
        create_version: bool = True,
        source: str = "user",
        dismiss_stale: bool = False,
    ) -> dict:
        """Lưu bản nháp hiện tại và (tùy chọn) ghi thêm một phiên bản lịch sử."""
        files_map = self.resolve_files()
        if stem not in files_map:
            raise FileNotFoundError(f"Không tìm thấy file '{stem}'.")

        ws = FileWorkspace(self.cfg.work_dir, files_map[stem])
        st = ws.load_state()
        prev_status = st.get("status")
        prev_style = nfc(str((st.get("label") or {}).get("value") or "")).strip()
        new_style = nfc(str(style or "")).strip()
        latest_snap = ws.latest_draft_version()
        ref_snap = latest_snap if create_version else None
        ref_ext = copy.deepcopy((ref_snap or st).get("extract") or {})
        ref_ab = copy.deepcopy((ref_snap or st).get("abstract") or {})
        units_data = ws.load_units()
        if not units_data:
            units_data = step_inspect(ws, self.cfg, st)
        if units_data and "total_words" in (units_data.get("budget") or {}):
            units_data["budget"] = compute_budget(units_data["budget"]["total_words"], self.cfg)

        self._apply_draft_fields(
            st,
            units_data,
            style,
            extract,
            abstract,
            selections=selections,
            custom_edited=custom_edited,
            cfg=self.cfg,
        )
        chk = run_checks(
            units_data,
            st["label"]["value"],
            st["extract"]["text"],
            st["abstract"]["text"],
            self.cfg,
        )
        st["checks"] = chk.to_dict()
        if st["status"] not in (STATUS_APPROVED, STATUS_EXPORTED):
            st["status"] = STATUS_DRAFT if chk.ok else STATUS_PENDING

        if dismiss_stale:
            st["stale_steps"] = []
            st["stale_reason"] = None
        elif new_style and prev_style and new_style != prev_style:
            baseline_style = nfc(str(((latest_snap or {}).get("label") or {}).get("value") or "")).strip()
            latest_clean = bool(latest_snap is not None and not (latest_snap.get("stale_steps") or []))
            if (
                latest_clean
                and new_style == baseline_style
                and str(st.get("stale_reason") or "").startswith("Nhãn phong cách đã thay đổi")
            ):
                st["stale_steps"] = []
                st["stale_reason"] = None
            else:
                _mark_stale_after(
                    st,
                    "label",
                    reason=f"Nhãn phong cách đã thay đổi từ '{prev_style}' sang '{new_style}'",
                )

        if create_version and st.get("stale_steps"):
            stale = list(st.get("stale_steps") or [])
            cur_ext_txt = nfc(str((st.get("extract") or {}).get("text") or "")).strip()
            ref_ext_txt = nfc(str(ref_ext.get("text") or "")).strip()
            cur_ext_sel = _normalize_selections_sig((st.get("extract") or {}).get("selections"))
            ref_ext_sel = _normalize_selections_sig(ref_ext.get("selections"))
            if "extract" in stale and (cur_ext_txt != ref_ext_txt or cur_ext_sel != ref_ext_sel):
                stale.remove("extract")
            cur_ab_txt = nfc(str((st.get("abstract") or {}).get("text") or "")).strip()
            ref_ab_txt = nfc(str(ref_ab.get("text") or "")).strip()
            if "abstract" in stale and cur_ab_txt != ref_ab_txt:
                stale.remove("abstract")
            st["stale_steps"] = stale
            if not stale:
                st["stale_reason"] = None

        if create_version:
            ver = ws.save_draft_version(
                st,
                source=source,
                note=nfc(note).strip() or "Lưu thủ công",
            )
            saved_new_version = True
        else:
            ver = int(st.get("current_version") or 0)
            saved_new_version = False

        ws.save_state(st)
        if saved_new_version or st.get("status") != prev_status:
            write_reports(self.cfg)
        prev_snap = ws.find_previous_version(st)
        return {
            "version": ver,
            "saved_new_version": saved_new_version,
            "has_unsaved_changes": ws.differs_from_latest_version(st),
            "previous_version": prev_snap.get("version") if prev_snap else None,
            "state": st,
            "versions": ws.list_draft_versions(),
            "checks": chk.to_dict(),
        }

    def restore_draft(
        self,
        stem: str,
        version: int | None = None,
        *,
        current_draft: dict | None = None,
    ) -> dict:
        """Khôi phục một phiên bản nháp từ lịch sử (hoặc lùi về version trước nếu version=None)."""
        files_map = self.resolve_files()
        if stem not in files_map:
            raise FileNotFoundError(f"Không tìm thấy file '{stem}'.")
        docx_path = files_map[stem]
        ws = FileWorkspace(self.cfg.work_dir, docx_path)
        st = ws.load_state()
        units_data = ws.load_units()
        if not units_data and docx_path.exists():
            units_data = step_inspect(ws, self.cfg, st)
        if units_data and "total_words" in (units_data.get("budget") or {}):
            units_data["budget"] = compute_budget(units_data["budget"]["total_words"], self.cfg)

        # Đồng bộ bản sửa tay mới nhất trên trình duyệt (nếu chưa kịp auto-save) để không mất khi rollback
        if isinstance(current_draft, dict):
            self._apply_draft_fields(
                st,
                units_data,
                style=str(current_draft.get("style", (st.get("label") or {}).get("value", ""))),
                extract=str(current_draft.get("extract", (st.get("extract") or {}).get("text", ""))),
                abstract=str(current_draft.get("abstract", (st.get("abstract") or {}).get("text", ""))),
                selections=current_draft.get("selections"),
                custom_edited=current_draft.get("custom_edited"),
                cfg=self.cfg,
            )

        if version is None:
            prev_snap = ws.find_previous_version(st)
            if not prev_snap:
                raise ValueError("Chưa có phiên bản trước đó để rollback.")
            target_ver = int(prev_snap["version"])
            snap = prev_snap
        else:
            target_ver = int(version)
            snap = ws.get_draft_version(target_ver)
            if not snap:
                raise ValueError(f"Không tìm thấy phiên bản v{target_ver:03d}.")

        # Bảo toàn các chỉnh sửa chưa tạo mốc version trước khi ghi đè state bằng bản khôi phục
        if ws.differs_from_latest_version(st):
            ws.save_draft_version(
                st,
                source="auto:before_restore",
                note=f"Tự động lưu trước khi rollback về v{target_ver:03d}",
            )

        defaults = ws.default_state()
        if "label" in snap and isinstance(snap["label"], dict):
            lb = copy.deepcopy(defaults["label"])
            lb.update(copy.deepcopy(snap["label"]))
            st["label"] = lb
        if "extract" in snap and isinstance(snap["extract"], dict):
            ex = copy.deepcopy(defaults["extract"])
            ex.update(copy.deepcopy(snap["extract"]))
            ex_text = nfc(ex.get("text") or "")
            ex["text"] = ex_text
            ex["words"] = count_words(ex_text)
            total_w = (units_data.get("budget") or {}).get("total_words", 0) if units_data else 0
            if total_w:
                ex["compress"] = round(ex["words"] / total_w, 4)
            st["extract"] = ex
        if "abstract" in snap and isinstance(snap["abstract"], dict):
            ab = copy.deepcopy(defaults["abstract"])
            ab.update(copy.deepcopy(snap["abstract"]))
            ab_text = nfc(ab.get("text") or "")
            ab["text"] = ab_text
            ab["words"] = count_words(ab_text)
            st["abstract"] = ab
        if "judge" in snap:
            st["judge"] = copy.deepcopy(snap["judge"])
        if "flags" in snap:
            st["flags"] = copy.deepcopy(snap.get("flags") or [])
        else:
            st["flags"] = [
                f for f in (st.get("flags") or [])
                if not str(f).startswith("Gọi Gemini thất bại")
                and not str(f).startswith("Bỏ qua bước tự chấm G-Eval")
            ]
        # Rollback là chủ động khôi phục một snapshot lịch sử cụ thể; không kế thừa
        # cảnh báo tạm (stale_steps / stale_reason) từ thời điểm AI vừa chạy một bước đơn lẻ.
        st["stale_steps"] = []
        st["stale_reason"] = None
        st["error_message"] = None
        st["current_step"] = None
        sync_manual_label_metadata(st, (st.get("label") or {}).get("value", ""), self.cfg)
        if "model_used" in snap:
            st["model_used"] = snap.get("model_used") or ""
        else:
            st["model_used"] = (
                (st.get("abstract") or {}).get("model_used")
                or (st.get("extract") or {}).get("model_used")
                or (st.get("label") or {}).get("model_used")
                or ""
            )

        if units_data:
            finalize_status(units_data, st, self.cfg, ignore_mismatch=True)
            if st.get("status") not in (STATUS_APPROVED, STATUS_EXPORTED):
                st["output_path"] = None

        new_ver = ws.save_draft_version(
            st,
            source="restore",
            note=f"Khôi phục từ v{target_ver:03d}",
            restored_from=target_ver,
        )
        ws.save_state(st)
        write_reports(self.cfg)
        prev_after = ws.find_previous_version(st)
        return {
            "version": new_ver,
            "restored_from": target_ver,
            "has_unsaved_changes": False,
            "previous_version": prev_after.get("version") if prev_after else None,
            "state": st,
            "versions": ws.list_draft_versions(),
        }

    def approve_and_export(
        self,
        stem: str,
        style: str,
        extract: str,
        abstract: str,
        confirmed_warnings: bool = False,
        allow_overwrite: bool = True,
    ) -> dict:
        """Duyệt bài và xuất file docx vào thư mục output_dir."""
        files_map = self.resolve_files()
        if stem not in files_map:
            raise FileNotFoundError(f"Không tìm thấy file '{stem}'.")
        docx_path = files_map[stem]
        ws = FileWorkspace(self.cfg.work_dir, docx_path)
        st = ws.load_state()
        units_data = ws.load_units() or step_inspect(ws, self.cfg, st)

        style = nfc(style).strip()
        extract = nfc(extract)
        abstract = nfc(abstract)

        self._apply_draft_fields(st, units_data, style, extract, abstract, cfg=self.cfg)
        chk = run_checks(units_data, style, extract, abstract, self.cfg)
        st["checks"] = chk.to_dict()

        if not chk.ok:
            ws.save_state(st)
            return {
                "ok": False,
                "error": f"Còn {len(chk.errors)} LỖI cơ học, chưa thể duyệt và ghi file.",
                "checks": chk.to_dict(),
            }

        if chk.warnings and not confirmed_warnings:
            return {
                "ok": False,
                "need_confirm": True,
                "message": f"Bài làm không có lỗi nhưng còn {len(chk.warnings)} CẢNH BÁO. Bạn có chắc muốn duyệt và ghi file .docx?",
                "checks": chk.to_dict(),
            }

        out_path = self.cfg.output_dir / docx_path.name
        fill_info = fill_docx(
            docx_path,
            out_path,
            style,
            extract,
            abstract,
            protected_dirs=[self.cfg.input_dir],
            allow_overwrite_content=allow_overwrite,
        )

        st["status"] = STATUS_EXPORTED if out_path.exists() else STATUS_APPROVED
        st["output_path"] = str(out_path)
        st["stale_steps"] = []
        st["stale_reason"] = None
        latest_snap = ws.latest_draft_version()
        if latest_snap is not None and not ws.differs_from_latest_version(st):
            latest_ver = int(latest_snap["version"])
            ws.mark_version_approved(latest_ver, status=st["status"], checks=st["checks"])
            st["current_version"] = latest_ver
        else:
            ws.save_draft_version(
                st,
                source="approve",
                note="Đã duyệt và xuất .docx",
                approved=True,
            )
        ws.save_state(st)
        mem = record_approved_sample(self.cfg.work_dir, units_data, st, style, extract, abstract)
        write_reports(self.cfg)
        prev_snap = ws.find_previous_version(st)
        return {
            "ok": True,
            "fill": fill_info,
            "state": st,
            "versions": ws.list_draft_versions(),
            "has_unsaved_changes": False,
            "previous_version": prev_snap.get("version") if prev_snap else None,
            "learned_count": mem.get("total_approved_learned", 0),
        }

    def export_batch(
        self,
        targets: list[str] | list[Path] | None = None,
        include_drafts: bool = False,
    ) -> dict:
        """Xuất hàng loạt các file theo danh sách hoặc toàn bộ file đã duyệt."""
        known = self.resolve_files()
        if targets:
            selected = []
            for t in targets:
                stem = Path(t).stem
                if stem in known:
                    selected.append(known[stem])
                elif Path(t).exists():
                    selected.append(Path(t).resolve())
        else:
            selected = list(known.values())

        allowed_statuses = {STATUS_APPROVED, STATUS_EXPORTED}
        if include_drafts:
            allowed_statuses.add(STATUS_DRAFT)

        exported, skipped, failed = 0, 0, 0
        details: list[dict] = []
        for docx_path in selected:
            ws = FileWorkspace(self.cfg.work_dir, docx_path)
            st = ws.load_state()
            if not targets and st["status"] not in allowed_statuses:
                skipped += 1
                continue
            units_data = ws.load_units() or step_inspect(ws, self.cfg, st)
            style = (st.get("label") or {}).get("value", "")
            extract = (st.get("extract") or {}).get("text", "")
            abstract = (st.get("abstract") or {}).get("text", "")
            chk = run_checks(units_data, style, extract, abstract, self.cfg)
            if not chk.ok:
                failed += 1
                details.append({"file": docx_path.name, "status": "blocked", "errors": len(chk.errors)})
                continue
            out_path = self.cfg.output_dir / docx_path.name
            try:
                info = fill_docx(
                    docx_path,
                    out_path,
                    style,
                    extract,
                    abstract,
                    protected_dirs=[self.cfg.input_dir],
                    allow_overwrite_content=True,
                )
                st["status"] = STATUS_EXPORTED
                st["output_path"] = str(out_path)
                ws.save_state(st)
                exported += 1
                details.append({"file": docx_path.name, "status": "exported", "info": info})
            except DocxError as exc:
                failed += 1
                details.append({"file": docx_path.name, "status": "error", "error": str(exc)})

        write_reports(self.cfg)
        return {
            "exported": exported,
            "skipped": skipped,
            "failed": failed,
            "details": details,
        }

    def delete_files(self, stems: list[str], mode: str = "selected") -> list[str]:
        """Xóa file khỏi input, output, và work."""
        files_map = self.resolve_files()
        if mode == "all":
            target_stems = list(files_map.keys())
        elif mode in ("pending", "approved"):
            target_stems = []
            for stem, src_path in files_map.items():
                ws = FileWorkspace(self.cfg.work_dir, src_path)
                st = ws.load_state()
                is_app = st["status"] in (STATUS_APPROVED, STATUS_EXPORTED)
                if (mode == "approved" and is_app) or (mode == "pending" and not is_app):
                    target_stems.append(stem)
        else:
            target_stems = [s for s in stems if s in files_map or (self.cfg.work_dir / s).exists()]

        deleted: list[str] = []
        for stem in target_stems:
            inp_file = self.cfg.input_dir / f"{stem}.docx"
            out_file = self.cfg.output_dir / f"{stem}.docx"
            work_sub = self.cfg.work_dir / stem
            if inp_file.exists():
                inp_file.unlink(missing_ok=True)
            if out_file.exists():
                out_file.unlink(missing_ok=True)
            if work_sub.exists() and work_sub.is_dir():
                shutil.rmtree(work_sub, ignore_errors=True)
            deleted.append(f"{stem}.docx")

        write_reports(self.cfg)
        return deleted

    def get_status_rows(self) -> list[dict]:
        """Thu thập danh sách trạng thái chi tiết của tất cả các file."""
        return collect_rows(self.cfg)

    def get_learned_count(self) -> int:
        """Lấy tổng số mẫu bài đã duyệt được hệ thống tự học."""
        mem = load_memory(self.cfg.work_dir)
        return int(mem.get("total_approved_learned", 0))

    def get_file_detail(self, stem: str) -> dict:
        """Lấy chi tiết state, units (kèm kiểm tra cơ học mới nhất) và danh sách phiên bản của một file."""
        files_map = self.resolve_files()
        if stem not in files_map:
            raise FileNotFoundError(f"Không tìm thấy file '{stem}'.")
        docx_path = files_map[stem]
        ws, st, units_data = self.inspect_file(docx_path)
        sync_manual_label_metadata(st, (st.get("label") or {}).get("value", ""), self.cfg)
        if units_data:
            chk = run_checks(
                units_data,
                st.get("label", {}).get("value", ""),
                st.get("extract", {}).get("text", ""),
                st.get("abstract", {}).get("text", ""),
                self.cfg,
            )
            st["checks"] = chk.to_dict()
        step_models = infer_step_models_from_logs(ws, st)
        for step_name, m_name in step_models.items():
            if step_name == "label" and isinstance(st.get("label"), dict) and st["label"].get("manual_edited"):
                continue
            if isinstance(st.get(step_name), dict) and not st[step_name].get("model_used"):
                st[step_name]["model_used"] = m_name
        if not st.get("model_used"):
            inferred = _infer_model_from_logs(ws, st)
            if inferred:
                st["model_used"] = inferred
        prev_snap = ws.find_previous_version(st)
        return {
            "state": st,
            "units": units_data,
            "versions": ws.list_draft_versions(),
            "has_unsaved_changes": ws.differs_from_latest_version(st),
            "previous_version": prev_snap.get("version") if prev_snap else None,
        }

    def get_file_versions(self, stem: str) -> dict:
        """Lấy danh sách phiên bản lịch sử và gợi ý rollback của một file."""
        files_map = self.resolve_files()
        if stem not in files_map:
            raise FileNotFoundError(f"Không tìm thấy file '{stem}'.")
        ws = FileWorkspace(self.cfg.work_dir, files_map[stem])
        st = ws.load_state()
        prev_snap = ws.find_previous_version(st)
        return {
            "stem": stem,
            "file": st.get("file") or f"{stem}.docx",
            "current_version": int(st.get("current_version") or 0),
            "has_unsaved_changes": ws.differs_from_latest_version(st),
            "previous_version": prev_snap.get("version") if prev_snap else None,
            "versions": ws.list_draft_versions(),
        }

    def get_file_logs(
        self,
        stem: str,
        limit: int = 60,
        *,
        is_running: bool = False,
        server_started_epoch: float | None = None,
    ) -> dict:
        """Đọc danh sách log các lần gọi Gemini của một file (hỗ trợ theo dõi thời gian thực)."""
        import time

        ws = self.get_workspace(stem)
        st = ws.load_state()
        items = []
        now_ts = time.time()
        if ws.logs_dir.exists():
            all_files = [
                lp for lp in sorted(ws.logs_dir.glob("*.json"))
                if not lp.name.startswith(".") and not lp.name.endswith(".tmp")
            ]
            # Duyệt theo thứ tự thời gian tăng dần để tự động suy luận số thứ tự retry (net_try)
            # chính xác cho cả các file log cũ chưa lưu trường net_try.
            raw_entries: list[tuple[Path, dict]] = []
            for lp in all_files[-limit:]:
                d = read_json(lp)
                if d:
                    raw_entries.append((lp, d))

            inferred_try = 0
            prev_step_attempt: tuple[str, int] | None = None
            prev_had_error = False

            for lp, d in raw_entries:
                try:
                    step_name = str(d.get("step", ""))
                    attempt_num = int(d.get("attempt", 1) or 1)
                    cur_key = (step_name, attempt_num)
                    if cur_key == prev_step_attempt and prev_had_error:
                        inferred_try += 1
                    else:
                        inferred_try = 1
                    prev_step_attempt = cur_key
                    prev_had_error = bool(d.get("error"))

                    saved_net_try = d.get("net_try")
                    try:
                        parsed_net_try = int(saved_net_try) if saved_net_try is not None else 0
                    except (TypeError, ValueError):
                        parsed_net_try = 0
                    net_try = parsed_net_try if parsed_net_try >= 1 else inferred_try

                    in_prog = bool(d.get("in_progress", False))
                    started_epoch = float(d.get("started_epoch") or 0.0)
                    dur = d.get("duration_s", 0)
                    if in_prog and started_epoch > 0:
                        elapsed = round(max(0.0, now_ts - started_epoch), 1)
                        dur = elapsed
                        started_before_server = (
                            server_started_epoch is not None
                            and started_epoch < (server_started_epoch - 1.0)
                        )
                        actively_in_current_server = is_running and not started_before_server
                        if not actively_in_current_server:
                            model_name = str(d.get("model") or "")
                            logged_timeout = d.get("timeout_s")
                            try:
                                if (
                                    isinstance(logged_timeout, (int, float))
                                    and not isinstance(logged_timeout, bool)
                                    and float(logged_timeout) > 0
                                ):
                                    model_timeout = float(logged_timeout)
                                else:
                                    cfg_timeout = get_model_timeout_s(model_name, self.cfg) if model_name else None
                                    if cfg_timeout is not None:
                                        model_timeout = cfg_timeout
                                    else:
                                        model_timeout = (
                                            DEFAULT_TIMEOUT_S.get(model_name, 180.0) if model_name else 180.0
                                        )
                            except (TypeError, ValueError):
                                model_timeout = DEFAULT_TIMEOUT_S.get(model_name, 180.0) if model_name else 180.0
                            wait_s_val = float(d.get("wait_s") or 0.0)
                            # Ngưỡng phát hiện log treo phải lớn hơn timeout thực tế của model (+30s đệm)
                            # cộng thời gian chờ giãn cách RPM để tránh báo lỗi giả khi API vẫn đang chạy;
                            # nếu file đang chạy lại trong server hiện tại mà gặp log có từ trước khi server khởi động -> chắc chắn log cũ đã chết.
                            stale_after_s = max(180.0, model_timeout + wait_s_val + 30.0, wait_s_val + 60.0)
                            if elapsed > stale_after_s or (is_running and started_before_server):
                                in_prog = False
                                if not d.get("error"):
                                    d["error"] = "Quá thời gian chờ hoặc tiến trình cũ đã kết thúc"
                    items.append({
                        "filename": lp.name,
                        "started_at": d.get("started_at", ""),
                        "started_epoch": started_epoch,
                        "step": step_name,
                        "attempt": attempt_num,
                        "net_try": net_try,
                        "model": d.get("model", ""),
                        "key_label": d.get("key_label", ""),
                        "in_progress": in_prog,
                        "status": d.get("status", ""),
                        "action": d.get("action", ""),
                        "phase": d.get("phase", ""),
                        "background": bool(d.get("background", False)),
                        "wait_s": d.get("wait_s", 0),
                        "duration_s": dur,
                        "usage": d.get("usage") or {},
                        "error": d.get("error", ""),
                        "response_text": d.get("response_text", ""),
                        "prompt": d.get("prompt", ""),
                    })
                except Exception:  # noqa: BLE001
                    pass
            items.reverse()  # Mới nhất lên đầu
        return {
            "stem": stem,
            "file": st.get("file") or f"{stem}.docx",
            "status": st.get("status", ""),
            "status_vi": st.get("status_vi", ""),
            "current_step": st.get("current_step") or "",
            "logs": items,
            "log_dir": str(ws.logs_dir),
        }

    def check_content(self, stem: str, style: str, extract: str, abstract: str) -> dict:
        """Kiểm tra cơ học tức thì cho nội dung nhãn / trích chọn / biên soạn của một file."""
        files_map = self.resolve_files()
        if stem not in files_map:
            raise FileNotFoundError(f"Không tìm thấy file '{stem}'.")
        ws = self.get_workspace(files_map[stem])
        units_data = ws.load_units()
        if not units_data:
            raise ValueError("File chưa được inspect.")
        if "total_words" in (units_data.get("budget") or {}):
            units_data["budget"] = compute_budget(units_data["budget"]["total_words"], self.cfg)
        chk = run_checks(units_data, style, extract, abstract, self.cfg)
        return {"checks": chk.to_dict()}

    def assemble_extract_from_selections(
        self, stem: str, selections: dict, style: str = "", abstract: str = ""
    ) -> dict:
        """Ghép bản trích chọn từ danh sách ID đơn vị được chọn và chạy kiểm tra cơ học."""
        files_map = self.resolve_files()
        if stem not in files_map:
            raise FileNotFoundError(f"Không tìm thấy file '{stem}'.")
        ws = self.get_workspace(files_map[stem])
        units_data = ws.load_units()
        if not units_data:
            raise ValueError("File chưa được inspect.")
        if "total_words" in (units_data.get("budget") or {}):
            units_data["budget"] = compute_budget(units_data["budget"]["total_words"], self.cfg)
        sel_clean, id_errors = normalize_selections(units_data, selections)
        text, per_doc_words = assemble_extract(units_data, sel_clean)
        chk = run_checks(units_data, style, text, abstract, self.cfg)
        return {
            "extract": text,
            "selections": {str(k): v for k, v in sel_clean.items()},
            "per_doc_words": {str(k): v for k, v in per_doc_words.items()},
            "id_errors": id_errors,
            "checks": chk.to_dict(),
        }

    def write_reports(self, out_xlsx: Path | None = None) -> tuple[Path, Path]:
        """Ghi báo cáo Excel và CSV."""
        return write_reports(self.cfg, out_xlsx=out_xlsx)
