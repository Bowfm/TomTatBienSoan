"""Batch service — quản lý tiến trình chạy AI hàng loạt ở tầng server."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
from typing import Callable

from ..config import Config, get_api_keys
from ..llm import GeminiClient, MockClient
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
    """Khởi tạo hoặc trả về Gemini client đã lưu trong cache."""
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
        files_map = self._files_resolver(self.cfg)
        client = self._client_factory(self.cfg, force_mock=use_mock)

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
