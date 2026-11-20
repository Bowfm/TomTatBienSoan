"""Batch service — quản lý tiến trình chạy AI hàng loạt ở tầng server."""
from __future__ import annotations

from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
import threading
import time
from typing import Callable
import uuid

from .. import config as _cfg_mod
from ..config import Config, clamp_workers, get_pool_scaling
from ..llm import (
    BaseClient,
    GeminiClient,
    MockClient,
    bind_cancel_event,
    get_key_rpd_details,
    get_rpd_status,
    unlock_quota_on_disk,
)
from ..llm.base import _safe_key_id
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


def get_live_client_if_any(cfg: Config | None = None) -> GeminiClient | None:
    """Trả về GeminiClient đang chạy trong RAM (nếu đã được khởi tạo), đồng bộ nóng keys/config nếu cần."""
    with _CLIENT_LOCK:
        if _CACHED_CLIENT is None:
            return None
        keys = _cfg_mod.get_api_keys()
        if keys and _CACHED_KEYS != keys:
            _CACHED_CLIENT.sync_keys(keys)
            _CACHED_KEYS[:] = list(keys)
        if cfg is not None:
            _CACHED_CLIENT.sync_config(cfg)
        return _CACHED_CLIENT


def _resolve_key_by_id(keys: list[str], key_id: str | None) -> str | None:
    if not key_id:
        return None
    for k in keys:
        if _safe_key_id(k) == key_id:
            return k
    return None


def get_rpd_details(cfg: Config, api_keys: list[str] | None = None) -> dict:
    """Lấy bảng trạng thái chi tiết còn/hết RPD của từng model theo từng key."""
    if api_keys is None:
        api_keys = _cfg_mod.get_api_keys()
    live = get_live_client_if_any(cfg)
    return get_key_rpd_details(cfg, api_keys=api_keys, live_client=live)


def unlock_rpd_quota(
    cfg: Config,
    *,
    key_id: str | None = None,
    model: str | None = None,
) -> dict:
    """Mở khóa trạng thái hết RPD / strikes_429 / cooldown trên cả RAM (_CACHED_CLIENT) và đĩa."""
    keys = _cfg_mod.get_api_keys()
    target_key: str | None = None
    if key_id:
        target_key = _resolve_key_by_id(keys, key_id)
        if target_key is None:
            raise ValueError("Không tìm thấy API key cần mở khóa trong cấu hình hiện hành.")

    live = get_live_client_if_any(cfg)
    if live is not None:
        unlocked = live.unlock_quota(key=target_key, model=model)
    else:
        unlocked = unlock_quota_on_disk(cfg, api_keys=keys, key=target_key, model=model)

    return {
        "ok": True,
        "unlocked": unlocked,
        "rpd_status": get_rpd_status(cfg, api_keys=keys),
        "details": get_rpd_details(cfg, api_keys=keys),
    }


def probe_rpd_key_model(
    cfg: Config,
    *,
    key_id: str,
    model: str,
    force_mock: bool = False,
) -> dict:
    """Gửi 1 request kiểm tra thực tế cho cặp (key_id, model) để đối chiếu với Google AI Studio."""
    if force_mock:
        raise RuntimeError("Đang chạy ở chế độ giả lập (--mock), không thể kiểm tra API thật.")
    keys = _cfg_mod.get_api_keys()
    target_key = _resolve_key_by_id(keys, key_id)
    if target_key is None:
        raise ValueError("Không tìm thấy API key cần kiểm tra trong cấu hình hiện hành.")
    client = make_client(cfg, force_mock=False)
    res = client.probe_key_model(target_key, model)
    res["key_id"] = key_id
    res["rpd_status"] = get_rpd_status(cfg, api_keys=keys)
    res["details"] = get_rpd_details(cfg, api_keys=keys)
    return res


class BatchManager:
    """Quản lý tiến trình chạy AI ở tầng server bằng ThreadPoolExecutor và hàng chờ thống nhất.

    - Giải phóng hoàn toàn kết nối HTTP của trình duyệt (tránh giới hạn 6 socket
      của Chrome khiến giao diện bị đơ/lag, không ấn được nút Duyệt hoặc Xem log).
    - Điều phối một ngân sách luồng chung (`<= allowed_workers`) cho cả chạy hàng loạt
      lẫn chạy lại từng file từ 5 nút AI: khi đã chạy đủ số luồng cho phép, yêu cầu chạy lại
      từng file sẽ vào hàng chờ và tự chạy ngay khi 1 luồng đang chạy hoàn thành (không bao giờ
      tạo thêm luồng thứ N+1).
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
        self._slot_cv = threading.Condition(self.lock)
        self.is_active = False
        self.stop_requested = False
        self.total = 0
        self.done_count = 0
        self.err_count = 0
        self.stems: list[str] = []
        self.running_stems: set[str] = set()
        self.rerun_stems: dict[str, str] = {}
        self._queued_reruns: list[dict] = []
        self._rerun_jobs: dict[str, dict] = {}
        self._active_job_id_by_stem: dict[str, str] = {}
        self._recent_rerun_results: dict[str, dict] = {}
        self._batch_pending: deque[str] = deque()
        self._batch_requested_concurrency: int | None = None
        self._batch_cancelled_by_user: set[str] = set()
        self._cancel_events: dict[str, threading.Event] = {}
        self._stopping_stems: set[str] = set()
        self.completed_stems: list[str] = []
        self.cancelled_stems: list[str] = []
        self.errors: dict[str, str] = {}
        self.executor: ThreadPoolExecutor | None = None

    def _allowed_workers_unlocked(self, requested_concurrency: int | None = None) -> int:
        limit = clamp_workers(None, self.cfg)
        if self.is_active and self._batch_requested_concurrency:
            limit = min(limit, self._batch_requested_concurrency)
        if requested_concurrency is not None:
            limit = min(limit, clamp_workers(requested_concurrency, self.cfg))
        return max(1, limit)

    def _active_slots_unlocked(self) -> int:
        return len(self.running_stems | set(self.rerun_stems.keys()))

    def _is_stem_queued_unlocked(self, stem: str) -> bool:
        return any(j["stem"] == stem for j in self._queued_reruns)

    def _remove_queued_job_unlocked(self, job_or_stem: dict | str) -> dict | None:
        if isinstance(job_or_stem, str):
            for idx, item in enumerate(self._queued_reruns):
                if item["stem"] == job_or_stem:
                    return self._queued_reruns.pop(idx)
            return None
        target_id = job_or_stem.get("job_id")
        for idx, item in enumerate(self._queued_reruns):
            if item["job_id"] == target_id:
                return self._queued_reruns.pop(idx)
        return None

    def _record_finished_rerun(self, job: dict, payload: dict) -> None:
        with self.lock:
            stem = job["stem"]
            active_id = self._active_job_id_by_stem.get(stem)
            if active_id is not None and active_id != job["job_id"]:
                return
            if active_id == job["job_id"]:
                self._active_job_id_by_stem.pop(stem, None)
            summary = {
                "job_id": job["job_id"],
                "stem": stem,
                "step": job["step"],
                "ok": bool(payload.get("ok")),
                "cancelled": bool(payload.get("cancelled")),
                "error": payload.get("error") or "",
                "message": payload.get("message") or "",
                "label_changed": bool(payload.get("label_changed")),
                "previous_label": payload.get("previous_label", ""),
                "new_label": payload.get("new_label", ""),
                "stale_steps": list(((payload.get("state") or {}).get("stale_steps")) or []),
                "completed_at": time.time(),
            }
            self._recent_rerun_results[stem] = summary
            if len(self._recent_rerun_results) > 50:
                oldest = sorted(
                    self._recent_rerun_results.items(),
                    key=lambda kv: kv[1].get("completed_at", 0.0),
                )[:-50]
                for old_k, _ in oldest:
                    self._recent_rerun_results.pop(old_k, None)
            if len(self._rerun_jobs) > 100:
                done_ids = [
                    jid for jid, j in self._rerun_jobs.items()
                    if j["done_ev"].is_set() and jid != job["job_id"]
                ]
                for jid in done_ids[:-50]:
                    self._rerun_jobs.pop(jid, None)

    def get_allowed_workers(self, requested_concurrency: int | None = None) -> int:
        with self.lock:
            return self._allowed_workers_unlocked(requested_concurrency)

    def get_queue_position(self, job_id: str) -> int:
        with self.lock:
            for idx, item in enumerate(self._queued_reruns, start=1):
                if item["job_id"] == job_id:
                    return idx
            return 0

    def start(self, stems: list[str], concurrency: int | None = None, use_mock: bool = False) -> dict:
        with self._slot_cv:
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
            self._batch_pending = deque(stems)
            self._batch_cancelled_by_user.clear()
            self.completed_stems = []
            self.cancelled_stems = []
            self.errors = {}
            scaling = get_pool_scaling(self.cfg)
            # Luôn kẹp theo trần cứng max_workers (chặn cả khi gọi API trực tiếp với concurrency lớn)
            requested_conc = clamp_workers(concurrency, self.cfg)
            self._batch_requested_concurrency = requested_conc
            eff_concurrency = max(1, min(requested_conc, len(stems)))
            BaseClient.ensure_concurrency_limit(max(requested_conc, scaling["max_concurrent_calls"]))
            self.executor = ThreadPoolExecutor(max_workers=eff_concurrency, thread_name_prefix="tomtat_batch")
            self._slot_cv.notify_all()

        threading.Thread(target=self._run_all, args=(stems, use_mock), daemon=True).start()
        return {"ok": True, "message": f"Đã khởi động xử lý {len(stems)} file trên {eff_concurrency} luồng (pool {scaling['key_count']} keys)."}

    def _run_all(self, stems: list[str], use_mock: bool):
        files_map = self._files_resolver(self.cfg)
        client = self._client_factory(self.cfg, force_mock=use_mock)

        def _process_one(stem: str, cancel_ev: threading.Event):
            try:
                if cancel_ev.is_set() or self.stop_requested:
                    with self._slot_cv:
                        if stem not in self.cancelled_stems:
                            self.cancelled_stems.append(stem)
                    return
                docx_path = files_map.get(stem) or (self.cfg.input_dir / f"{stem}.docx")
                with bind_cancel_event(cancel_ev):
                    res = process_file(docx_path, self.cfg, client, force=True, ignore_mismatch=False)
                with self._slot_cv:
                    if isinstance(res, dict) and res.get("cancelled"):
                        if stem not in self.cancelled_stems:
                            self.cancelled_stems.append(stem)
                    else:
                        self.done_count += 1
                        self.completed_stems.append(stem)
            except Exception as e:
                with self._slot_cv:
                    self.err_count += 1
                    self.errors[stem] = str(e)
            finally:
                with self._slot_cv:
                    self.running_stems.discard(stem)
                    self._cancel_events.pop(stem, None)
                    self._stopping_stems.discard(stem)
                    self._slot_cv.notify_all()

        # Điều phối từng file qua pool dùng chung: luôn nhường lượt cho các file đang chờ từ 5 nút AI
        # và tuyệt đối không vượt tổng số luồng cho phép (running_stems + rerun_stems <= allowed_workers).
        in_flight: set[Future] = set()
        while True:
            done_futs = {f for f in in_flight if f.done()}
            if done_futs:
                for f in done_futs:
                    try:
                        f.result()
                    except Exception:
                        pass
                in_flight.difference_update(done_futs)

            with self._slot_cv:
                if self.stop_requested:
                    self._batch_pending.clear()
                    if not in_flight:
                        break
                else:
                    if self._batch_cancelled_by_user and self._batch_pending:
                        self._batch_pending = deque(
                            s for s in self._batch_pending
                            if s not in self._batch_cancelled_by_user
                        )
                    if not self._batch_pending and not in_flight:
                        break
                    allowed = self._allowed_workers_unlocked()
                    exec_cap = self.executor._max_workers if self.executor else allowed
                    while (
                        not self.stop_requested
                        and self._batch_pending
                        and not self._queued_reruns
                        and self._active_slots_unlocked() < allowed
                        and len(self.running_stems) < exec_cap
                    ):
                        chosen = None
                        for cand in self._batch_pending:
                            if (
                                cand not in self.running_stems
                                and cand not in self.rerun_stems
                                and not self._is_stem_queued_unlocked(cand)
                            ):
                                chosen = cand
                                break
                        if chosen is None:
                            break
                        self._batch_pending.remove(chosen)
                        self.running_stems.add(chosen)
                        cancel_ev = threading.Event()
                        self._cancel_events[chosen] = cancel_ev
                        self._stopping_stems.discard(chosen)
                        fut = self.executor.submit(_process_one, chosen, cancel_ev)
                        in_flight.add(fut)
                self._slot_cv.wait(timeout=0.05)

        for f in list(in_flight):
            try:
                f.result()
            except Exception:
                pass

        try:
            write_reports(self.cfg)
        except Exception:
            pass

        with self._slot_cv:
            self.is_active = False
            self._batch_requested_concurrency = None
            self._batch_pending.clear()
            self._batch_cancelled_by_user.clear()
            self.running_stems.clear()
            if self.executor:
                self.executor.shutdown(wait=False)
                self.executor = None
            self._slot_cv.notify_all()

    def try_acquire_file(self, stem: str, step: str = "") -> tuple[bool, str | None]:
        """Đăng ký khóa chạy AI đơn lẻ cho 1 file; trả về (False, lý do) nếu file đang chạy hoặc đang chờ."""
        with self._slot_cv:
            if stem in self.running_stems:
                return False, f"File '{stem}.docx' đang được AI xử lý trong tiến trình chạy hàng loạt."
            if stem in self.rerun_stems:
                return False, f"File '{stem}.docx' đang được AI xử lý. Vui lòng đợi hoàn tất."
            if self._is_stem_queued_unlocked(stem):
                return False, f"File '{stem}.docx' đang chờ lượt chạy AI. Vui lòng đợi hoàn tất."
            self.rerun_stems[stem] = step or "all"
            self._cancel_events[stem] = threading.Event()
            self._stopping_stems.discard(stem)
            self._slot_cv.notify_all()
            return True, None

    def reserve_rerun(
        self,
        stem: str,
        step: str = "",
        *,
        concurrency: int | None = None,
    ) -> tuple[bool, str | None, dict | None]:
        """Đăng ký chạy lại AI cho 1 file theo đúng ngân sách luồng dùng chung.

        - Nếu còn slot trống và chưa có file nào đợi trước: chiếm ngay 1 slot chạy (`rerun_stems`).
        - Nếu đang chạy đủ số luồng cho phép: đưa vào hàng chờ FIFO (`_queued_reruns`), không tạo luồng phụ.
        """
        with self._slot_cv:
            if stem in self.running_stems:
                return False, f"File '{stem}.docx' đang được AI xử lý trong tiến trình chạy hàng loạt.", None
            if stem in self.rerun_stems:
                return False, f"File '{stem}.docx' đang được AI xử lý. Vui lòng đợi hoàn tất.", None
            if self._is_stem_queued_unlocked(stem):
                return False, f"File '{stem}.docx' đang chờ lượt chạy AI. Vui lòng đợi hoàn tất.", None

            self._recent_rerun_results.pop(stem, None)
            allowed = self._allowed_workers_unlocked(concurrency)
            can_run_now = (not self._queued_reruns) and (self._active_slots_unlocked() < allowed)
            cancel_ev = threading.Event()
            done_ev = threading.Event()
            job_id = uuid.uuid4().hex[:12]
            eff_step = step or "all"
            job = {
                "job_id": job_id,
                "stem": stem,
                "step": eff_step,
                "queued_at": time.time(),
                "cancel_ev": cancel_ev,
                "done_ev": done_ev,
                "status": "running" if can_run_now else "queued",
                "cancelled": False,
                "concurrency": concurrency,
                "result": None,
                "status_code": 200,
            }
            self._rerun_jobs[job_id] = job
            self._active_job_id_by_stem[stem] = job_id
            self._cancel_events[stem] = cancel_ev
            self._stopping_stems.discard(stem)
            if can_run_now:
                self.rerun_stems[stem] = eff_step
            else:
                self._queued_reruns.append(job)
            self._slot_cv.notify_all()

        BaseClient.ensure_concurrency_limit(allowed)
        return True, None, job

    def abort_unstarted_rerun(self, job_id: str) -> None:
        """Hủy đăng ký job rerun khi gặp lỗi trước lúc bắt đầu thực thi (ví dụ thiếu API key)."""
        with self._slot_cv:
            job = self._rerun_jobs.pop(job_id, None)
            if not job:
                return
            stem = job["stem"]
            cancel_ev = job["cancel_ev"]
            if self._active_job_id_by_stem.get(stem) == job_id:
                self._active_job_id_by_stem.pop(stem, None)
                self.rerun_stems.pop(stem, None)
            self._remove_queued_job_unlocked(job)
            if self._cancel_events.get(stem) is cancel_ev:
                self._cancel_events.pop(stem, None)
                self._stopping_stems.discard(stem)
            job["done_ev"].set()
            self._slot_cv.notify_all()

    def run_rerun_job(
        self,
        job: dict,
        runner: Callable[[threading.Event], tuple[dict, int]],
        on_cancelled: Callable[[], tuple[dict, int]],
    ) -> tuple[dict, int]:
        """Chờ đến lượt (nếu đang xếp hàng), chạy `runner(cancel_ev)` và giải phóng slot an toàn."""
        stem = job["stem"]
        cancel_ev: threading.Event = job["cancel_ev"]
        acquired_slot = (job["status"] == "running")
        concurrency = job.get("concurrency")
        try:
            if not acquired_slot:
                with self._slot_cv:
                    while True:
                        if cancel_ev.is_set() or job.get("cancelled"):
                            self._remove_queued_job_unlocked(job)
                            if self._cancel_events.get(stem) is cancel_ev:
                                self._cancel_events.pop(stem, None)
                                self._stopping_stems.discard(stem)
                            job["status"] = "cancelled"
                            self._slot_cv.notify_all()
                            break
                        allowed = self._allowed_workers_unlocked(concurrency)
                        is_head = bool(
                            self._queued_reruns
                            and self._queued_reruns[0]["job_id"] == job["job_id"]
                        )
                        if is_head and self._active_slots_unlocked() < allowed:
                            self._queued_reruns.pop(0)
                            self.rerun_stems[stem] = job["step"]
                            job["status"] = "running"
                            acquired_slot = True
                            self._slot_cv.notify_all()
                            break
                        self._slot_cv.wait(timeout=0.1)

            if not acquired_slot:
                payload, code = on_cancelled()
                job["result"] = payload
                job["status_code"] = code
                self._record_finished_rerun(job, payload)
                return payload, code

            if cancel_ev.is_set() or job.get("cancelled"):
                payload, code = on_cancelled()
                job["status"] = "cancelled"
                job["result"] = payload
                job["status_code"] = code
                self._record_finished_rerun(job, payload)
                return payload, code

            payload, code = runner(cancel_ev)
            job["status"] = (
                "cancelled"
                if payload.get("cancelled")
                else ("done" if payload.get("ok") else "error")
            )
            job["result"] = payload
            job["status_code"] = code
            self._record_finished_rerun(job, payload)
            return payload, code
        except Exception as exc:  # noqa: BLE001
            err_payload = {"error": str(exc)}
            job["status"] = "error"
            job["result"] = err_payload
            job["status_code"] = 500
            self._record_finished_rerun(job, err_payload)
            return err_payload, 500
        finally:
            with self._slot_cv:
                if acquired_slot and self._active_job_id_by_stem.get(stem) in (None, job["job_id"]):
                    self.rerun_stems.pop(stem, None)
                else:
                    self._remove_queued_job_unlocked(job)
                if self._cancel_events.get(stem) is cancel_ev:
                    self._cancel_events.pop(stem, None)
                    self._stopping_stems.discard(stem)
                if self._active_job_id_by_stem.get(stem) == job["job_id"]:
                    self._active_job_id_by_stem.pop(stem, None)
                self._slot_cv.notify_all()
            job["done_ev"].set()

    def get_cancel_event(self, stem: str) -> threading.Event | None:
        """Lấy Event hủy gắn với một file đang chạy hoặc đang chờ (nếu có)."""
        with self.lock:
            return self._cancel_events.get(stem)

    def release_file(self, stem: str) -> None:
        """Giải phóng khóa chạy AI đơn lẻ cho 1 file."""
        with self._slot_cv:
            self.rerun_stems.pop(stem, None)
            self._remove_queued_job_unlocked(stem)
            self._cancel_events.pop(stem, None)
            self._stopping_stems.discard(stem)
            self._slot_cv.notify_all()

    def stop_file(self, stem: str) -> dict:
        """Gửi yêu cầu dừng tiến trình AI đang chạy hoặc đang chờ lượt cho một file cụ thể."""
        with self._slot_cv:
            queued_job = self._remove_queued_job_unlocked(stem)
            if queued_job is not None:
                queued_job["cancelled"] = True
                queued_job["cancel_ev"].set()
                if stem in self._batch_pending:
                    self._batch_cancelled_by_user.add(stem)
                    if stem not in self.cancelled_stems:
                        self.cancelled_stems.append(stem)
                self._cancel_events.pop(stem, None)
                self._stopping_stems.discard(stem)
                self._slot_cv.notify_all()
                return {
                    "ok": True,
                    "stem": stem,
                    "stopped": True,
                    "was_queued": True,
                    "message": f"Đã hủy chờ lượt chạy AI cho {stem}.docx.",
                }
            ev = self._cancel_events.get(stem)
            is_running = (stem in self.running_stems) or (stem in self.rerun_stems)
            if ev is not None or is_running:
                if ev is None:
                    ev = threading.Event()
                    self._cancel_events[stem] = ev
                ev.set()
                self._stopping_stems.add(stem)
                self._slot_cv.notify_all()
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
        with self._slot_cv:
            if self.is_active:
                self.stop_requested = True
            self._batch_pending.clear()
            active_stems = sorted(set(self.running_stems) | set(self.rerun_stems))
            queued_stems: list[str] = []
            while self._queued_reruns:
                q_job = self._queued_reruns.pop(0)
                q_stem = q_job["stem"]
                queued_stems.append(q_stem)
                q_job["cancelled"] = True
                q_job["cancel_ev"].set()
                self._cancel_events.pop(q_stem, None)
                self._stopping_stems.discard(q_stem)
            for stem in active_stems:
                ev = self._cancel_events.get(stem)
                if ev is None:
                    ev = threading.Event()
                    self._cancel_events[stem] = ev
                ev.set()
                self._stopping_stems.add(stem)
            self._slot_cv.notify_all()
            all_stopped = sorted(set(active_stems) | set(queued_stems))
            return {
                "ok": True,
                "stopped_stems": all_stopped,
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
            allowed_workers = self._allowed_workers_unlocked()
            queued_reruns = [
                {
                    "job_id": j["job_id"],
                    "stem": j["stem"],
                    "step": j["step"],
                    "position": idx,
                    "queued_at": j["queued_at"],
                }
                for idx, j in enumerate(self._queued_reruns, start=1)
            ]
            batch_pending_count = len(self._batch_pending)
            recent_results = {k: dict(v) for k, v in self._recent_rerun_results.items()}

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
                if s not in running_updated:
                    running_updated[s] = str(st.get("updated_at") or "")
            except Exception:
                rerun_steps[s] = str(fallback_step or "")

        all_running = sorted(set(running_list) | set(rerun_dict.keys()))
        all_running_steps = {**rerun_steps, **running_steps}
        queued_stems = [q["stem"] for q in queued_reruns]
        queued_steps = {q["stem"]: q["step"] for q in queued_reruns}
        queued_positions = {q["stem"]: q["position"] for q in queued_reruns}

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
            "allowed_workers": allowed_workers,
            "active_worker_count": len(all_running),
            "queued_reruns": queued_reruns,
            "queued_rerun_stems": queued_stems,
            "queued_rerun_steps": queued_steps,
            "queued_rerun_positions": queued_positions,
            "batch_pending_count": batch_pending_count,
            "recent_rerun_results": recent_results,
            "stopping_stems": stopping_list,
            "completed": completed_list,
            "cancelled": cancelled_list,
            "errors": errors_dict,
            "stopped": stopped,
            "api_key_count": len(keys),
            "pool_scaling": scaling,
            "rpd_status": get_rpd_status(self.cfg, api_keys=keys),
        }
