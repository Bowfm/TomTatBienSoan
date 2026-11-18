"""FileService — Tầng nghiệp vụ xử lý, duyệt, lưu trữ và xuất file tài liệu."""
from __future__ import annotations

from datetime import datetime
import io
import json
from pathlib import Path
import shutil
import zipfile

from ..checks import run_checks
from ..config import Config
from ..docx_io import DocxError, fill_docx
from ..memory import load_memory, record_approved_sample
from ..models import FileState, UnitsData
from ..pipeline import Pipeline, finalize_status, process_file, step_inspect
from ..report import _infer_model_from_logs, collect_rows, infer_step_models_from_logs, write_reports
from ..state import (
    STATUS_APPROVED,
    STATUS_DRAFT,
    STATUS_EXPORTED,
    STATUS_PENDING,
    FileWorkspace,
    discover_docx_files,
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
        user_note: str = "",
        ignore_mismatch: bool = False,
        restart: bool = False,
    ) -> dict:
        """Chạy pipeline AI cho một file docx."""
        pipeline = Pipeline(self.cfg, client)
        return pipeline.process(
            docx_path,
            force=force,
            only_step=only_step,
            user_note=user_note,
            ignore_mismatch=ignore_mismatch,
            restart=restart,
        )

    async def process_async(
        self,
        docx_path: Path,
        client,
        force: bool = False,
        only_step: str | None = None,
        user_note: str = "",
        ignore_mismatch: bool = False,
        restart: bool = False,
    ) -> dict:
        """Chạy pipeline AI bất đồng bộ (asyncio) cho một file docx."""
        pipeline = Pipeline(self.cfg, client)
        return await pipeline.process_async(
            docx_path,
            force=force,
            only_step=only_step,
            user_note=user_note,
            ignore_mismatch=ignore_mismatch,
            restart=restart,
        )

    def save_draft(
        self,
        stem: str,
        style: str,
        extract: str,
        abstract: str,
        selections: dict | None = None,
        note: str = "",
        custom_edited: bool = False,
    ) -> dict:
        """Lưu bản nháp hiện tại và ghi thêm một phiên bản lịch sử."""
        files_map = self.resolve_files()
        if stem not in files_map:
            raise FileNotFoundError(f"Không tìm thấy file '{stem}'.")

        ws = FileWorkspace(self.cfg.work_dir, files_map[stem])
        st = ws.load_state()
        units_data = ws.load_units()
        if not units_data:
            units_data = step_inspect(ws, self.cfg, st)
        if units_data and "total_words" in (units_data.get("budget") or {}):
            units_data["budget"] = compute_budget(units_data["budget"]["total_words"], self.cfg)

        style = nfc(style).strip()
        extract = nfc(extract)
        abstract = nfc(abstract)
        selections = selections or st.get("extract", {}).get("selections") or {}

        st.setdefault("label", {})["value"] = style
        total = (units_data.get("budget") or {}).get("total_words", 0)
        n_ext = count_words(extract)
        st["extract"].update({
            "selections": selections,
            "text": extract,
            "words": n_ext,
            "compress": round(n_ext / total, 4) if total else 0.0,
            "custom_edited": custom_edited,
        })
        st["abstract"].update({
            "text": abstract,
            "words": count_words(abstract),
        })
        chk = run_checks(units_data, style, extract, abstract, self.cfg)
        st["checks"] = chk.to_dict()
        if st["status"] not in (STATUS_APPROVED, STATUS_EXPORTED):
            st["status"] = STATUS_DRAFT if chk.ok else STATUS_PENDING

        ver = ws.save_draft_version(st, source="user", note=note or "Lưu nháp thủ công")
        ws.save_state(st)
        write_reports(self.cfg)
        return {
            "version": ver,
            "state": st,
            "versions": ws.list_draft_versions(),
            "checks": chk.to_dict(),
        }

    def restore_draft(self, stem: str, version: int) -> dict:
        """Khôi phục một phiên bản nháp từ lịch sử."""
        files_map = self.resolve_files()
        if stem not in files_map:
            raise FileNotFoundError(f"Không tìm thấy file '{stem}'.")
        ws = FileWorkspace(self.cfg.work_dir, files_map[stem])
        snap = ws.get_draft_version(version)
        if not snap:
            raise ValueError(f"Không tìm thấy phiên bản v{version:03d}.")
        st = ws.load_state()
        units_data = ws.load_units()
        st["label"] = snap.get("label") or st["label"]
        st["extract"].update(snap.get("extract") or {})
        st["abstract"].update(snap.get("abstract") or {})
        if snap.get("judge") is not None:
            st["judge"] = snap["judge"]
        if units_data:
            finalize_status(units_data, st, self.cfg, ignore_mismatch=True)
        new_ver = ws.save_draft_version(st, source="restore", note=f"Khôi phục từ v{version:03d}")
        ws.save_state(st)
        write_reports(self.cfg)
        return {
            "version": new_ver,
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

        chk = run_checks(units_data, style, extract, abstract, self.cfg)
        st["label"]["value"] = style
        st["extract"]["text"] = extract
        st["extract"]["words"] = count_words(extract)
        st["abstract"]["text"] = abstract
        st["abstract"]["words"] = count_words(abstract)
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
        ws.save_draft_version(st, source="approve", note="Đã duyệt và xuất .docx")
        ws.save_state(st)
        mem = record_approved_sample(self.cfg.work_dir, units_data, st, style, extract, abstract)
        write_reports(self.cfg)
        return {
            "ok": True,
            "fill": fill_info,
            "state": st,
            "versions": ws.list_draft_versions(),
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

    def create_zip_export(self, stems: list[str] | None = None) -> tuple[str, bytes]:
        """Đóng gói file ZIP các bài đã hoàn thành."""
        files_map = self.resolve_files()
        ready_paths: list[Path] = []
        for stem, src_path in sorted(files_map.items()):
            if stems and stem not in stems:
                continue
            ws = FileWorkspace(self.cfg.work_dir, src_path)
            st = ws.load_state()
            out_path = self.cfg.output_dir / src_path.name
            if st["status"] in (STATUS_APPROVED, STATUS_EXPORTED) and out_path.exists():
                ready_paths.append(out_path)

        if not ready_paths:
            raise ValueError("Chưa có file nào đã duyệt/đã xuất để tải ZIP.")

        today_tag = datetime.now().strftime("%d-%m")
        zip_name = f"{today_tag}.zip"

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for p in ready_paths:
                zf.write(p, arcname=p.name)

        self.cfg.output_dir.mkdir(parents=True, exist_ok=True)
        (self.cfg.output_dir / zip_name).write_bytes(buf.getvalue())
        return zip_name, buf.getvalue()

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
            if isinstance(st.get(step_name), dict) and not st[step_name].get("model_used"):
                st[step_name]["model_used"] = m_name
        if not st.get("model_used"):
            inferred = _infer_model_from_logs(ws, st)
            if inferred:
                st["model_used"] = inferred
        return {
            "state": st,
            "units": units_data,
            "versions": ws.list_draft_versions(),
        }

    def get_file_logs(self, stem: str, limit: int = 40) -> dict:
        """Đọc danh sách log các lần gọi Gemini của một file."""
        ws = self.get_workspace(stem)
        items = []
        if ws.logs_dir.exists():
            for lp in sorted(ws.logs_dir.glob("*.json"), reverse=True)[:limit]:
                try:
                    d = json.loads(lp.read_text(encoding="utf-8"))
                    items.append({
                        "filename": lp.name,
                        "started_at": d.get("started_at", ""),
                        "step": d.get("step", ""),
                        "attempt": d.get("attempt", 1),
                        "net_try": d.get("net_try", 0),
                        "model": d.get("model", ""),
                        "duration_s": d.get("duration_s", 0),
                        "usage": d.get("usage") or {},
                        "error": d.get("error", ""),
                        "response_text": d.get("response_text", ""),
                        "prompt": d.get("prompt", ""),
                    })
                except Exception:  # noqa: BLE001
                    pass
        return {"logs": items, "log_dir": str(ws.logs_dir)}

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
