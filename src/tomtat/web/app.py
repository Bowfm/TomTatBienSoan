"""Flask server cho giao diện duyệt local (chỉ lắng nghe trên 127.0.0.1)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import io
from pathlib import Path
import re
import shutil
import threading
import zipfile

from flask import Flask, jsonify, render_template, request, send_file

from .. import STYLES
from ..checks import run_checks
from ..config import Config, get_api_key, get_api_keys, load_config
from ..docx_io import DocxError, fill_docx
from ..llm import GeminiClient, MockClient
from ..memory import load_memory, record_approved_sample
from ..pipeline import finalize_status, process_file, step_inspect
from ..report import _infer_model_from_logs, collect_rows, infer_step_models_from_logs, write_reports
from ..state import (
    STATUS_APPROVED,
    STATUS_DRAFT,
    STATUS_EXPORTED,
    STATUS_MANUAL,
    STATUS_PARTIAL,
    STATUS_VI,
    FileWorkspace,
    discover_docx_files,
)
from ..textutil import configure as configure_textutil, count_words, nfc
from ..units import assemble_extract, normalize_selections


def _sync_config_non_paths(target_cfg: Config) -> None:
    try:
        fresh = load_config()
        for k in ("extract", "abstract", "grading", "gemini", "word_count", "sentence_split", "judge", "workers"):
            if k in fresh:
                target_cfg[k] = fresh[k]
    except Exception:
        pass


def _resolve_files(cfg: Config) -> dict[str, Path]:
    files = {p.stem: p for p in discover_docx_files(cfg.input_dir)}
    if cfg.work_dir.exists():
        for d in sorted(cfg.work_dir.iterdir()):
            if d.is_dir() and (d / "state.json").exists() and d.name not in files:
                ws = FileWorkspace(cfg.work_dir, d / f"{d.name}.docx")
                st = ws.load_state()
                src = Path(st.get("source_path") or (cfg.input_dir / f"{d.name}.docx"))
                if src.exists():
                    files[d.name] = src
    return files


_CLIENT_LOCK = threading.Lock()
_CACHED_CLIENT: GeminiClient | None = None
_CACHED_KEYS: list[str] = []


def _make_client(cfg: Config, force_mock: bool = False):
    if force_mock:
        return MockClient(cfg)
    keys = get_api_keys()
    if not keys:
        raise RuntimeError(
            "Chưa đặt GEMINI_API_KEY (trong biến môi trường, file .env hoặc api_keys.txt). "
            "Hãy cấu hình API key hoặc chạy review với cờ --mock."
        )
    global _CACHED_CLIENT, _CACHED_KEYS
    with _CLIENT_LOCK:
        if _CACHED_CLIENT is None or _CACHED_KEYS != keys:
            _CACHED_CLIENT = GeminiClient(cfg, keys)
            _CACHED_KEYS = list(keys)
        return _CACHED_CLIENT


class BatchManager:
    """Quản lý tiến trình chạy AI hàng loạt ở tầng server bằng ThreadPoolExecutor.

    Giải phóng hoàn toàn kết nối HTTP của trình duyệt (tránh giới hạn 6 socket
    của Chrome khiến giao diện bị đơ/lag, không ấn được nút Duyệt hoặc Xem log).
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.is_active = False
        self.stop_requested = False
        self.total = 0
        self.done_count = 0
        self.err_count = 0
        self.stems: list[str] = []
        self.running_stems: set[str] = set()
        self.completed_stems: list[str] = []
        self.errors: dict[str, str] = {}
        self.executor: ThreadPoolExecutor | None = None

    def start(self, stems: list[str], concurrency: int = 8, use_mock: bool = False) -> dict:
        with self.lock:
            if self.is_active:
                return {"ok": False, "message": "Tiến trình batch đang chạy dở."}
            if not stems:
                return {"ok": False, "message": "Danh sách file rỗng."}
            self.is_active = True
            self.stop_requested = False
            self.stems = list(stems)
            self.total = len(stems)
            self.done_count = 0
            self.err_count = 0
            self.running_stems = set()
            self.completed_stems = []
            self.errors = {}
            concurrency = max(1, min(concurrency, len(stems)))
            self.executor = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="tomtat_batch")

        threading.Thread(target=self._run_all, args=(stems, use_mock), daemon=True).start()
        return {"ok": True, "message": f"Đã khởi động xử lý {len(stems)} file trên {concurrency} luồng."}

    def _run_all(self, stems: list[str], use_mock: bool):
        files_map = _resolve_files(self.cfg)
        client = _make_client(self.cfg, force_mock=use_mock)

        def _process_one(stem: str):
            if self.stop_requested:
                return
            with self.lock:
                self.running_stems.add(stem)

            try:
                docx_path = files_map.get(stem) or (self.cfg.input_dir / f"{stem}.docx")
                process_file(docx_path, self.cfg, client, force=True, ignore_mismatch=False)
                with self.lock:
                    self.done_count += 1
                    self.completed_stems.append(stem)
            except Exception as e:
                with self.lock:
                    self.err_count += 1
                    self.errors[stem] = str(e)
            finally:
                with self.lock:
                    self.running_stems.discard(stem)

        # Chạy từng file qua thread pool theo đúng thứ tự stems
        futures = []
        for s in stems:
            if self.stop_requested:
                break
            futures.append(self.executor.submit(_process_one, s))

        for f in futures:
            try:
                f.result()
            except Exception:
                pass

        try:
            write_reports(self.cfg)
        except Exception:
            pass

        with self.lock:
            self.is_active = False
            self.running_stems.clear()
            if self.executor:
                self.executor.shutdown(wait=False)
                self.executor = None

    def stop(self) -> dict:
        with self.lock:
            self.stop_requested = True
            return {"ok": True, "message": "Đã gửi tín hiệu dừng tới các luồng AI."}

    def status(self) -> dict:
        with self.lock:
            return {
                "active": self.is_active,
                "total": self.total,
                "done_count": self.done_count,
                "err_count": self.err_count,
                "running": list(self.running_stems),
                "completed": list(self.completed_stems),
                "errors": dict(self.errors),
                "stopped": self.stop_requested,
            }


def create_app(cfg: Config, mock: bool = False) -> Flask:
    here = Path(__file__).parent
    app = Flask(
        __name__,
        template_folder=str(here / "templates"),
        static_folder=str(here / "static"),
    )
    app.config["JSON_AS_ASCII"] = False
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
    sc = cfg.get("sentence_split", {})
    configure_textutil(sc.get("abbreviations"), sc.get("heading_words"))

    @app.get("/")
    def index():
        return render_template("index.html", styles=STYLES, status_vi=STATUS_VI, mock=mock)

    batch_mgr = BatchManager(cfg)

    @app.post("/api/batch/start")
    def api_batch_start():
        payload = request.get_json(force=True) or {}
        stems = payload.get("stems") or []
        concurrency = int(payload.get("concurrency") or 8)
        use_mock = bool(payload.get("mock", mock))
        return jsonify(batch_mgr.start(stems, concurrency, use_mock))

    @app.get("/api/batch/status")
    def api_batch_status():
        return jsonify(batch_mgr.status())

    @app.post("/api/batch/stop")
    def api_batch_stop():
        return jsonify(batch_mgr.stop())

    @app.get("/api/files")
    def api_files():
        fresh_cfg = load_config()
        files_map = _resolve_files(cfg)
        rows = collect_rows(cfg, list(files_map.values()))
        g = fresh_cfg.get("gemini", {})
        mem = load_memory(cfg.work_dir)
        return jsonify({
            "files": rows,
            "styles": STYLES,
            "status_vi": STATUS_VI,
            "mock": mock,
            "has_api_key": bool(get_api_key()),
            "api_key_count": len(get_api_keys()),
            "primary_model": g.get("model", ""),
            "fallback_models": g.get("fallback_models") or [],
            "learned_count": mem.get("total_approved_learned", 0),
        })

    @app.post("/api/upload")
    def api_upload():
        """Tải một hoặc nhiều file .docx từ trình duyệt vào thư mục input\\ (và tùy chọn chạy AI ngay)."""
        uploaded = request.files.getlist("files")
        if not uploaded:
            return jsonify({"error": "Chưa chọn file .docx nào."}), 400
        run_now = request.form.get("run_now", "false").lower() in ("1", "true", "yes", "on")
        use_mock = request.form.get("mock", "false").lower() in ("1", "true", "yes", "on") or mock
        overwrite = request.form.get("overwrite", "false").lower() in ("1", "true", "yes", "on")

        # Sắp xếp danh sách file nạp: file ở cuối sẽ lên đầu, file đầu sẽ về cuối (reverse=True)
        uploaded.sort(
            key=lambda f: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(getattr(f, "filename", "") or ""))],
            reverse=True,
        )

        cfg.input_dir.mkdir(parents=True, exist_ok=True)
        saved_paths: list[Path] = []
        skipped_existing: list[str] = []
        errors: list[str] = []

        for f in uploaded:
            raw_name = Path(nfc(f.filename or "")).name.strip()
            if not raw_name or not raw_name.lower().endswith(".docx") or raw_name.startswith("~$"):
                errors.append(f"Bỏ qua '{raw_name or '(trống)'}': chỉ nhận file .docx hợp lệ.")
                continue
            dst = cfg.input_dir / raw_name
            if dst.exists() and not overwrite:
                skipped_existing.append(raw_name)
                continue
            f.save(str(dst))
            saved_paths.append(dst)

        if skipped_existing and not overwrite:
            return jsonify({
                "need_overwrite": True,
                "existing": skipped_existing,
                "added": [p.name for p in saved_paths],
                "message": (
                    f"Có {len(skipped_existing)} file đã tồn tại trong danh sách "
                    f"({', '.join(skipped_existing[:5])}{'...' if len(skipped_existing) > 5 else ''}). "
                    "Bạn có muốn ghi đè và làm lại không?"
                ),
            })

        processed_stems: list[str] = []
        for dst in saved_paths:
            ws = FileWorkspace(cfg.work_dir, dst)
            st = ws.load_state()
            if overwrite:
                st = ws.default_state()
            try:
                step_inspect(ws, cfg, st)
            except DocxError as exc:
                errors.append(f"{dst.name}: {exc}")
                continue
            processed_stems.append(dst.stem)

        # Luôn sắp xếp thứ tự danh sách file theo thứ tự chuẩn từ trên xuống dưới
        processed_stems.sort()

        if run_now and processed_stems:
            try:
                client = _make_client(cfg, force_mock=use_mock)
                for stem in processed_stems:
                    dst = cfg.input_dir / f"{stem}.docx"
                    res = process_file(dst, cfg, client, force=True)
                    if res.get("error"):
                        errors.append(f"{dst.name}: {res['error']}")
            except RuntimeError as exc:
                errors.append(str(exc))

        write_reports(cfg)
        return jsonify({
            "ok": True,
            "added": sorted([p.name for p in saved_paths]),
            "stems": processed_stems,
            "skipped_existing": skipped_existing,
            "errors": errors,
        })

    @app.post("/api/process-batch")
    def api_process_batch():
        """Chạy AI cho tất cả các file chưa xử lý (hoặc danh sách chỉ định) ngay từ trình duyệt."""
        payload = request.get_json(force=True) or {}
        use_mock = bool(payload.get("mock", mock))
        force = bool(payload.get("force", False))
        stems = payload.get("stems") or []

        try:
            _sync_config_non_paths(cfg)
            client = _make_client(cfg, force_mock=use_mock)
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 400

        files_map = _resolve_files(cfg)
        targets = (
            [files_map[s] for s in stems if s in files_map]
            if stems
            else list(files_map.values())
        )
        ran, skipped, errs = [], [], []
        for p in targets:
            res = process_file(p, cfg, client, force=force)
            if res.get("skipped"):
                skipped.append(p.name)
            else:
                ran.append(p.name)
            if res.get("error"):
                errs.append(f"{p.name}: {res['error']}")
        write_reports(cfg)
        return jsonify({
            "ok": True,
            "ran": ran,
            "skipped": skipped,
            "errors": errs,
        })

    @app.get("/api/file/<stem>")
    def api_get_file(stem: str):
        _sync_config_non_paths(cfg)
        files_map = _resolve_files(cfg)
        if stem not in files_map:
            return jsonify({"error": f"Không tìm thấy file '{stem}'."}), 404
        docx_path = files_map[stem]
        ws = FileWorkspace(cfg.work_dir, docx_path)
        st = ws.load_state()
        units_data = ws.load_units()
        if units_data is None and docx_path.exists():
            try:
                units_data = step_inspect(ws, cfg, st)
            except DocxError as exc:
                return jsonify({"error": str(exc)}), 400
        if units_data:
            from ..units import compute_budget
            if "total_words" in (units_data.get("budget") or {}):
                units_data["budget"] = compute_budget(units_data["budget"]["total_words"], cfg)
            chk = run_checks(
                units_data,
                st.get("label", {}).get("value", ""),
                st.get("extract", {}).get("text", ""),
                st.get("abstract", {}).get("text", ""),
                cfg,
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
        return jsonify({
            "state": st,
            "units": units_data,
            "versions": ws.list_draft_versions(),
        })

    @app.get("/api/file/<stem>/logs")
    def api_get_logs(stem: str):
        """Trả về danh sách các lần gọi Gemini (logs) của file để xem trực tiếp trên web."""
        ws = FileWorkspace(cfg.work_dir, cfg.input_dir / f"{stem}.docx")
        items = []
        if ws.logs_dir.exists():
            import json
            for lp in sorted(ws.logs_dir.glob("*.json"), reverse=True)[:40]:
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
        return jsonify({"logs": items, "log_dir": str(ws.logs_dir)})

    @app.post("/api/file/<stem>/assemble")
    def api_assemble(stem: str):
        """Ghép bản trích chọn từ các ID đơn vị được tick trên giao diện."""
        files_map = _resolve_files(cfg)
        if stem not in files_map:
            return jsonify({"error": f"Không tìm thấy file '{stem}'."}), 404
        ws = FileWorkspace(cfg.work_dir, files_map[stem])
        units_data = ws.load_units()
        if not units_data:
            return jsonify({"error": "File chưa được inspect."}), 400
        from ..units import compute_budget
        if "total_words" in (units_data.get("budget") or {}):
            units_data["budget"] = compute_budget(units_data["budget"]["total_words"], cfg)
        payload = request.get_json(force=True) or {}
        selections = payload.get("selections") or {}
        style = payload.get("style") or ""
        abstract = payload.get("abstract") or ""
        sel_clean, id_errors = normalize_selections(units_data, selections)
        text, per_doc_words = assemble_extract(units_data, sel_clean)
        chk = run_checks(units_data, style, text, abstract, cfg)
        return jsonify({
            "extract": text,
            "selections": {str(k): v for k, v in sel_clean.items()},
            "per_doc_words": {str(k): v for k, v in per_doc_words.items()},
            "id_errors": id_errors,
            "checks": chk.to_dict(),
        })

    @app.post("/api/file/<stem>/check")
    def api_check(stem: str):
        """Kiểm tra tức thì khi người dùng sửa nhãn / trích chọn / biên soạn."""
        files_map = _resolve_files(cfg)
        if stem not in files_map:
            return jsonify({"error": f"Không tìm thấy file '{stem}'."}), 404
        ws = FileWorkspace(cfg.work_dir, files_map[stem])
        units_data = ws.load_units()
        if not units_data:
            return jsonify({"error": "File chưa được inspect."}), 400
        from ..units import compute_budget
        if "total_words" in (units_data.get("budget") or {}):
            units_data["budget"] = compute_budget(units_data["budget"]["total_words"], cfg)
        payload = request.get_json(force=True) or {}
        style = payload.get("style", "")
        extract = payload.get("extract", "")
        abstract = payload.get("abstract", "")
        chk = run_checks(units_data, style, extract, abstract, cfg)
        return jsonify({"checks": chk.to_dict()})

    @app.post("/api/file/<stem>/save")
    def api_save(stem: str):
        """Lưu bản nháp hiện tại và ghi thêm một phiên bản vào lịch sử."""
        files_map = _resolve_files(cfg)
        if stem not in files_map:
            return jsonify({"error": f"Không tìm thấy file '{stem}'."}), 404
        ws = FileWorkspace(cfg.work_dir, files_map[stem])
        st = ws.load_state()
        units_data = ws.load_units()
        if not units_data:
            units_data = step_inspect(ws, cfg, st)
        if units_data and "total_words" in (units_data.get("budget") or {}):
            from ..units import compute_budget
            units_data["budget"] = compute_budget(units_data["budget"]["total_words"], cfg)

        payload = request.get_json(force=True) or {}
        style = nfc(payload.get("style", "")).strip()
        extract = nfc(payload.get("extract", ""))
        abstract = nfc(payload.get("abstract", ""))
        selections = payload.get("selections") or st.get("extract", {}).get("selections") or {}
        note = nfc(payload.get("note", "")).strip()

        st.setdefault("label", {})["value"] = style
        total = units_data["budget"]["total_words"]
        n_ext = count_words(extract)
        st["extract"].update({
            "selections": selections,
            "text": extract,
            "words": n_ext,
            "compress": round(n_ext / total, 4) if total else 0.0,
            "custom_edited": bool(payload.get("custom_edited", False)),
        })
        st["abstract"].update({
            "text": abstract,
            "words": count_words(abstract),
        })
        chk = run_checks(units_data, style, extract, abstract, cfg)
        st["checks"] = chk.to_dict()
        if st["status"] not in (STATUS_APPROVED, STATUS_EXPORTED):
            st["status"] = STATUS_DRAFT if chk.ok else STATUS_PARTIAL
        ver = ws.save_draft_version(st, source="user", note=note or "Lưu nháp thủ công")
        ws.save_state(st)
        write_reports(cfg)
        return jsonify({
            "ok": True,
            "version": ver,
            "state": st,
            "versions": ws.list_draft_versions(),
        })

    @app.post("/api/file/<stem>/rerun")
    def api_rerun(stem: str):
        """Chạy lại một bước (hoặc toàn bộ) bằng AI kèm ghi chú của người duyệt."""
        files_map = _resolve_files(cfg)
        if stem not in files_map:
            return jsonify({"error": f"Không tìm thấy file '{stem}'."}), 404
        docx_path = files_map[stem]
        payload = request.get_json(force=True) or {}
        step = payload.get("step") or None
        user_note = nfc(payload.get("note", "")).strip()
        use_mock = bool(payload.get("mock", mock))
        ignore_mismatch = bool(payload.get("ignore_mismatch", False))
        restart = bool(payload.get("restart", False))

        # Lưu tạm các chỉnh sửa trên màn hình trước khi chạy bước tiếp theo
        ws = FileWorkspace(cfg.work_dir, docx_path)
        st = ws.load_state()
        if payload.get("style"):
            st.setdefault("label", {})["value"] = nfc(payload["style"]).strip()
        if payload.get("extract") is not None and step in ("abstract", "judge"):
            st.setdefault("extract", {})["text"] = nfc(payload["extract"])
            st["extract"]["words"] = count_words(st["extract"]["text"])
        if payload.get("abstract") is not None and step == "judge":
            st.setdefault("abstract", {})["text"] = nfc(payload["abstract"])
            st["abstract"]["words"] = count_words(st["abstract"]["text"])
        ws.save_state(st)

        try:
            _sync_config_non_paths(cfg)
            client = _make_client(cfg, force_mock=use_mock)
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 400

        out = process_file(
            docx_path,
            cfg,
            client,
            force=True,
            only_step=step if step != "all" else None,
            user_note=user_note,
            ignore_mismatch=ignore_mismatch,
            restart=restart,
        )
        write_reports(cfg)
        if out.get("error"):
            return jsonify({"error": out["error"], "state": out["state"]}), 400
        return jsonify({
            "ok": True,
            "state": out["state"],
            "units": ws.load_units(),
            "versions": ws.list_draft_versions(),
        })

    @app.post("/api/file/<stem>/restore")
    def api_restore(stem: str):
        """Khôi phục một phiên bản nháp trong lịch sử."""
        files_map = _resolve_files(cfg)
        if stem not in files_map:
            return jsonify({"error": f"Không tìm thấy file '{stem}'."}), 404
        ws = FileWorkspace(cfg.work_dir, files_map[stem])
        payload = request.get_json(force=True) or {}
        ver = int(payload.get("version", 0))
        snap = ws.get_draft_version(ver)
        if not snap:
            return jsonify({"error": f"Không tìm thấy phiên bản v{ver:03d}."}), 404
        st = ws.load_state()
        units_data = ws.load_units()
        st["label"] = snap.get("label") or st["label"]
        st["extract"].update(snap.get("extract") or {})
        st["abstract"].update(snap.get("abstract") or {})
        if snap.get("judge") is not None:
            st["judge"] = snap["judge"]
        if units_data:
            finalize_status(units_data, st, cfg, ignore_mismatch=True)
        new_ver = ws.save_draft_version(st, source="restore", note=f"Khôi phục từ v{ver:03d}")
        ws.save_state(st)
        write_reports(cfg)
        return jsonify({
            "ok": True,
            "version": new_ver,
            "state": st,
            "versions": ws.list_draft_versions(),
        })

    @app.post("/api/file/<stem>/approve")
    def api_approve(stem: str):
        """Duyệt và ghi ra output\\<file>.docx (chặn nếu còn LỖI)."""
        files_map = _resolve_files(cfg)
        if stem not in files_map:
            return jsonify({"error": f"Không tìm thấy file '{stem}'."}), 404
        docx_path = files_map[stem]
        ws = FileWorkspace(cfg.work_dir, docx_path)
        st = ws.load_state()
        units_data = ws.load_units() or step_inspect(ws, cfg, st)

        payload = request.get_json(force=True) or {}
        style = nfc(payload.get("style") or st["label"]["value"]).strip()
        extract = nfc(payload.get("extract") if payload.get("extract") is not None else st["extract"]["text"])
        abstract = nfc(payload.get("abstract") if payload.get("abstract") is not None else st["abstract"]["text"])
        confirmed_warnings = bool(payload.get("confirm_warnings", False))
        allow_overwrite = bool(payload.get("allow_overwrite_content", True))

        chk = run_checks(units_data, style, extract, abstract, cfg)
        st["label"]["value"] = style
        st["extract"]["text"] = extract
        st["extract"]["words"] = count_words(extract)
        st["abstract"]["text"] = abstract
        st["abstract"]["words"] = count_words(abstract)
        st["checks"] = chk.to_dict()

        if not chk.ok:
            ws.save_state(st)
            return jsonify({
                "error": f"Còn {len(chk.errors)} LỖI cơ học, chưa thể duyệt và ghi file.",
                "checks": chk.to_dict(),
            }), 400

        if chk.warnings and not confirmed_warnings:
            return jsonify({
                "need_confirm": True,
                "message": f"Bài làm không có lỗi nhưng còn {len(chk.warnings)} CẢNH BÁO. Bạn có chắc muốn duyệt và ghi file .docx?",
                "checks": chk.to_dict(),
            })

        out_path = cfg.output_dir / docx_path.name
        try:
            fill_info = fill_docx(
                docx_path,
                out_path,
                style,
                extract,
                abstract,
                protected_dirs=[cfg.input_dir],
                allow_overwrite_content=allow_overwrite,
            )
        except DocxError as exc:
            return jsonify({"error": str(exc)}), 400

        st["status"] = STATUS_EXPORTED if out_path.exists() else STATUS_APPROVED
        st["output_path"] = str(out_path)
        ws.save_draft_version(st, source="approve", note="Đã duyệt và xuất .docx")
        ws.save_state(st)
        # Lưu bài vừa duyệt vào Bộ nhớ Tự học (learned_memory.json) để AI thông minh hơn ở các bài sau
        mem = record_approved_sample(cfg.work_dir, units_data, st, style, extract, abstract)
        write_reports(cfg)
        return jsonify({
            "ok": True,
            "fill": fill_info,
            "state": st,
            "versions": ws.list_draft_versions(),
            "learned_count": mem.get("total_approved_learned", 0),
        })

    @app.get("/api/download-file/<stem>")
    def api_download_single(stem: str):
        """Tải lẻ một file .docx đã hoàn thành về trình duyệt."""
        out_path = cfg.output_dir / f"{stem}.docx"
        if not out_path.exists():
            return jsonify({"error": f"Chưa có file xuất '{out_path.name}' trong output\\."}), 404
        return send_file(
            str(out_path),
            as_attachment=True,
            download_name=out_path.name,
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

    @app.route("/api/download-zip", methods=["GET", "POST"])
    def api_download_zip():
        """Đóng gói các file .docx đã xong thành file ZIP tên theo ngày hôm nay (VD: 17-11.zip)."""
        stems_req: list[str] = []
        if request.method == "POST":
            payload = request.get_json(silent=True) or {}
            stems_req = [str(s).replace(".docx", "") for s in (payload.get("stems") or [])]
        else:
            raw = request.args.get("stems", "").strip()
            if raw:
                stems_req = [s.strip().replace(".docx", "") for s in raw.split(",") if s.strip()]

        files_map = _resolve_files(cfg)
        ready_paths: list[Path] = []
        for stem, src_path in sorted(files_map.items()):
            if stems_req and stem not in stems_req:
                continue
            ws = FileWorkspace(cfg.work_dir, src_path)
            st = ws.load_state()
            out_path = cfg.output_dir / src_path.name
            if st["status"] in (STATUS_APPROVED, STATUS_EXPORTED) and out_path.exists():
                ready_paths.append(out_path)

        if not ready_paths:
            return jsonify({"error": "Chưa có file nào đã duyệt/đã xuất để tải ZIP."}), 400

        today_tag = datetime.now().strftime("%d-%m")
        zip_name = f"{today_tag}.zip"

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for p in ready_paths:
                zf.write(p, arcname=p.name)
        # Lưu luôn một bản ZIP vào thư mục output\ để tiện lấy trực tiếp nếu cần
        cfg.output_dir.mkdir(parents=True, exist_ok=True)
        (cfg.output_dir / zip_name).write_bytes(buf.getvalue())
        buf.seek(0)
        return send_file(
            buf,
            as_attachment=True,
            download_name=zip_name,
            mimetype="application/zip",
        )

    @app.post("/api/delete-files")
    def api_delete_files():
        """Xóa một hoặc nhiều file (hoặc toàn bộ batch trong ngày) khỏi input\\, work\\ và output\\."""
        payload = request.get_json(force=True) or {}
        mode = payload.get("mode", "selected")  # selected | pending | approved | all
        req_stems = [str(s).replace(".docx", "") for s in (payload.get("stems") or [])]

        files_map = _resolve_files(cfg)
        target_stems: list[str] = []

        if mode == "all":
            target_stems = list(files_map.keys())
        elif mode in ("pending", "approved"):
            for stem, src_path in files_map.items():
                ws = FileWorkspace(cfg.work_dir, src_path)
                st = ws.load_state()
                is_app = st["status"] in (STATUS_APPROVED, STATUS_EXPORTED)
                if (mode == "approved" and is_app) or (mode == "pending" and not is_app):
                    target_stems.append(stem)
        else:
            target_stems = [s for s in req_stems if s in files_map or (cfg.work_dir / s).exists()]

        deleted: list[str] = []
        for stem in target_stems:
            inp_file = cfg.input_dir / f"{stem}.docx"
            out_file = cfg.output_dir / f"{stem}.docx"
            work_sub = cfg.work_dir / stem
            if inp_file.exists():
                inp_file.unlink(missing_ok=True)
            if out_file.exists():
                out_file.unlink(missing_ok=True)
            if work_sub.exists() and work_sub.is_dir():
                shutil.rmtree(work_sub, ignore_errors=True)
            deleted.append(f"{stem}.docx")

        write_reports(cfg)
        return jsonify({
            "ok": True,
            "deleted": deleted,
            "count": len(deleted),
        })

    @app.post("/api/self-update")
    def api_self_update():
        """Kéo bản cập nhật code/prompt mới nhất từ GitHub (qua cap_nhat.bat)."""
        import subprocess

        bat = cfg.root / "cap_nhat.bat"
        if not bat.exists():
            return jsonify({"error": "Không tìm thấy file cap_nhat.bat."}), 400
        proc = subprocess.run(
            ["cmd", "/c", str(bat)],
            cwd=str(cfg.root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
        )
        out_txt = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
        return jsonify({
            "ok": proc.returncode == 0,
            "output": out_txt.strip(),
        })

    return app
