"""Batch service — quản lý tiến trình chạy AI hàng loạt ở tầng server."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
from typing import Callable

from .. import config as _cfg_mod
from ..config import Config, clamp_workers, get_pool_scaling
from ..llm import BaseClient, GeminiClient, MockClient, bind_cancel_event, get_rpd_status
from ..pipeline import process_file
from ..report import write_reports
from ..state import FileWorkspace, discover_docx_files


def resolve_files(cfg: Config) -> dict[str, Path]:
    """Tìm tất cả các file .docx trong input và work directory."""
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


def make_client(cfg: Config, force_mock: bool = False):
    """Khởi tạo hoặc trả về Gemini client đã lưu trong cache (tự động đồng bộ nóng nếu thêm/bớt API key)."""
    if force_mock:
        return MockClient(cfg)
    keys = _cfg_mod.get_api_keys()
    if not keys:
        raise RuntimeError(
            "Chưa đặt GEMINI_API_KEY (trong biến môi trường, file .env hoặc api_keys.txt). "
            "Hãy cấu hình API key (xem .env.example) hoặc chạy thử với cờ --mock."
        )
    global _CACHED_CLIENT, _CACHED_KEYS
    with _CLIENT_LOCK:
        if _CACHED_CLIENT is None:
            _CACHED_CLIENT = GeminiClient(cfg, keys)
            _CACHED_KEYS = list(keys)
        else:
            if _CACHED_KEYS != keys:
                _CACHED_CLIENT.sync_keys(keys)
                _CACHED_KEYS = list(keys)
            _CACHED_CLIENT.sync_config(cfg)
        return _CACHED_CLIENT


class BatchManager:
    """Quản lý tiến trình chạy AI hàng loạt ở tầng server bằng ThreadPoolExecutor.

    Giải phóng hoàn toàn kết nối HTTP của trình duyệt (tránh giới hạn 6 socket
    của Chrome khiến giao diện bị đơ/lag, không ấn được nút Duyệt hoặc Xem log).
    """

    def __init__(
        self,
        cfg: Config,
        client_factory: Callable[[Config, bool], object] | None = None,
        files_resolver: Callable[[Config], dict[str, Path]] | None = None,
    ):
        self.cfg = cfg
        self._client_factory = client_factory or make_client
        self._files_resolver = files_resolver or resolve_files
        self.lock = threading.Lock()
        self.is_active = False
        self.stop_requested = False
        self.total = 0
        self.done_count = 0
        self.err_count = 0
        self.stems: list[str] = []
        self.running_stems: set[str] = set()
        self.rerun_stems: dict[str, str] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._stopping_stems: set[str] = set()
        self.completed_stems: list[str] = []
        self.cancelled_stems: list[str] = []
        self.errors: dict[str, str] = {}
        self.executor: ThreadPoolExecutor | None = None

    def start(self, stems: list[str], concurrency: int | None = None, use_mock: bool = False) -> dict:
        with self.lock:
            if self.is_active:
                return {"ok": False, "message": "Tiến trình batch đang chạy dở."}
            if not stems:
                return {"ok": False, "message": "Danh sách file rỗng."}
            self.cfg.reload()
            self.is_active = True
            self.stop_requested = False
            self.stems = list(stems)
            self.total = len(stems)
            self.done_count = 0
            self.err_count = 0
            self.running_stems = set()
            self.completed_stems = []
            self.cancelled_stems = []
            self.errors = {}
            scaling = get_pool_scaling(self.cfg)
            # Luôn kẹp theo trần cứng max_workers (chặn cả khi gọi API trực tiếp với concurrency lớn)
            eff_concurrency = clamp_workers(concurrency, self.cfg)
            eff_concurrency = max(1, min(eff_concurrency, len(stems)))
            BaseClient.ensure_concurrency_limit(max(eff_concurrency, scaling["max_concurrent_calls"]))
            self.executor = ThreadPoolExecutor(max_workers=eff_concurrency, thread_name_prefix="tomtat_batch")

        threading.Thread(target=self._run_all, args=(stems, use_mock), daemon=True).start()
        return {"ok": True, "message": f"Đã khởi động xử lý {len(stems)} file trên {eff_concurrency} luồng (pool {scaling['key_count']} keys)."}

    def _run_all(self, stems: list[str], use_mock: bool):
        files_map = self._files_resolver(self.cfg)
        client = self._client_factory(self.cfg, force_mock=use_mock)

        def _process_one(stem: str):
            with self.lock:
                if self.stop_requested:
                    return
                if stem in self.running_stems or stem in self.rerun_stems:
                    self.err_count += 1
                    self.errors[stem] = f"File '{stem}.docx' đang được AI xử lý ở luồng khác."
                    return
                self.running_stems.add(stem)
                cancel_ev = threading.Event()
                self._cancel_events[stem] = cancel_ev
                self._stopping_stems.discard(stem)

            try:
                docx_path = files_map.get(stem) or (self.cfg.input_dir / f"{stem}.docx")
                with bind_cancel_event(cancel_ev):
                    res = process_file(docx_path, self.cfg, client, force=True, ignore_mismatch=False)
                with self.lock:
                    if isinstance(res, dict) and res.get("cancelled"):
                        self.cancelled_stems.append(stem)
                    else:
                        self.done_count += 1
                        self.completed_stems.append(stem)
            except Exception as e:
                with self.lock:
                    self.err_count += 1
                    self.errors[stem] = str(e)
            finally:
                with self.lock:
                    self.running_stems.discard(stem)
                    self._cancel_events.pop(stem, None)
                    self._stopping_stems.discard(stem)

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

    def try_acquire_file(self, stem: str, step: str = "") -> tuple[bool, str | None]:
        """Đăng ký khóa chạy AI đơn lẻ cho 1 file; trả về (False, lý do) nếu file đang chạy."""
        with self.lock:
            if stem in self.running_stems:
                return False, f"File '{stem}.docx' đang được AI xử lý trong tiến trình chạy hàng loạt."
            if stem in self.rerun_stems:
                return False, f"File '{stem}.docx' đang được AI xử lý. Vui lòng đợi hoàn tất."
            self.rerun_stems[stem] = step or "all"
            self._cancel_events[stem] = threading.Event()
            self._stopping_stems.discard(stem)
            return True, None

    def get_cancel_event(self, stem: str) -> threading.Event | None:
        """Lấy Event hủy gắn với một file đang chạy (nếu có)."""
        with self.lock:
            return self._cancel_events.get(stem)

    def release_file(self, stem: str) -> None:
        """Giải phóng khóa chạy AI đơn lẻ cho 1 file."""
        with self.lock:
            self.rerun_stems.pop(stem, None)
            self._cancel_events.pop(stem, None)
            self._stopping_stems.discard(stem)

    def stop_file(self, stem: str) -> dict:
        """Gửi yêu cầu dừng tiến trình AI đang chạy cho một file cụ thể."""
        with self.lock:
            ev = self._cancel_events.get(stem)
            is_running = (stem in self.running_stems) or (stem in self.rerun_stems)
            if ev is not None or is_running:
                if ev is None:
                    ev = threading.Event()
                    self._cancel_events[stem] = ev
                ev.set()
                self._stopping_stems.add(stem)
                return {
                    "ok": True,
                    "stem": stem,
                    "stopped": True,
                    "message": f"Đã gửi yêu cầu dừng chạy AI cho {stem}.docx.",
                }
            return {
                "ok": True,
                "stem": stem,
                "stopped": False,
                "message": f"File {stem}.docx hiện không có tiến trình AI nào đang chạy.",
            }

    def stop(self) -> dict:
        with self.lock:
            self.stop_requested = True
            active_stems = sorted(set(self.running_stems) | set(self.rerun_stems))
            for stem in active_stems:
                ev = self._cancel_events.get(stem)
                if ev is None:
                    ev = threading.Event()
                    self._cancel_events[stem] = ev
                ev.set()
                self._stopping_stems.add(stem)
            return {
                "ok": True,
                "stopped_stems": active_stems,
                "message": "Đã gửi tín hiệu dừng tới các luồng AI.",
            }

    def status(self) -> dict:
        with self.lock:
            running_list = list(self.running_stems)
            rerun_dict = dict(self.rerun_stems)
            stopping_list = sorted(self._stopping_stems)
            stems_list = list(self.stems)
            completed_list = list(self.completed_stems)
            cancelled_list = list(self.cancelled_stems)
            errors_dict = dict(self.errors)
            is_active = self.is_active
            total = self.total
            done_count = self.done_count
            err_count = self.err_count
            stopped = self.stop_requested

        running_steps: dict[str, str] = {}
        running_updated: dict[str, str] = {}
        for s in running_list:
            try:
                ws = FileWorkspace(self.cfg.work_dir, self.cfg.input_dir / f"{s}.docx")
                st = ws.load_state()
                running_steps[s] = str(st.get("current_step") or "")
                running_updated[s] = str(st.get("updated_at") or "")
            except Exception:
                pass

        rerun_steps: dict[str, str] = {}
        for s, fallback_step in rerun_dict.items():
            try:
                ws = FileWorkspace(self.cfg.work_dir, self.cfg.input_dir / f"{s}.docx")
                st = ws.load_state()
                rerun_steps[s] = str(st.get("current_step") or fallback_step or "")
            except Exception:
                rerun_steps[s] = str(fallback_step or "")

        all_running = sorted(set(running_list) | set(rerun_dict.keys()))
        all_running_steps = {**rerun_steps, **running_steps}

        keys = _cfg_mod.get_api_keys()
        scaling = get_pool_scaling(self.cfg, api_keys=keys)
        return {
            "active": is_active,
            "total": total,
            "done_count": done_count,
            "err_count": err_count,
            "stems": stems_list,
            "running": running_list,
            "running_steps": running_steps,
            "running_updated": running_updated,
            "rerun_running": list(rerun_dict.keys()),
            "rerun_steps": rerun_steps,
            "all_running": all_running,
            "all_running_steps": all_running_steps,
            "stopping_stems": stopping_list,
            "completed": completed_list,
            "cancelled": cancelled_list,
            "errors": errors_dict,
            "stopped": stopped,
            "api_key_count": len(keys),
            "pool_scaling": scaling,
            "rpd_status": get_rpd_status(self.cfg, api_keys=keys),
        }
