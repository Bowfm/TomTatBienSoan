"""Kiểm thử tầng Services: FileService, BatchManager, Update & Architecture regression tests."""
from __future__ import annotations

import json
import time

from tomtat.config import Config, load_config
from tomtat.services import BatchManager, FileService
from tomtat.services.batch_service import make_client, resolve_files
from tomtat.updater import _save_version, check_update
from tomtat.web import create_app
from tomtat.web.app import create_app as create_app_direct


def test_api_batch_endpoints(tmp_path):
    cfg = load_config()
    cfg["paths"] = {
        "input": str(tmp_path / "input"),
        "output": str(tmp_path / "output"),
        "work": str(tmp_path / "work"),
        "prompts": str(cfg.prompts_dir),
        "report": str(tmp_path / "output" / "baocao.xlsx"),
    }
    sample = cfg.root / "samples" / "M54629.docx"
    inp = tmp_path / "input" / "M54629.docx"
    inp.parent.mkdir(parents=True, exist_ok=True)
    inp.write_bytes(sample.read_bytes())

    app = create_app(cfg, mock=True)
    tc = app.test_client()

    r_start = tc.post("/api/batch/start", json={"stems": ["M54629"], "concurrency": 2, "mock": True})
    assert r_start.status_code == 200
    assert r_start.get_json()["ok"] is True

    for _ in range(50):
        r_st = tc.get("/api/batch/status")
        assert r_st.status_code == 200
        data = r_st.get_json()
        if not data["active"]:
            break
        time.sleep(0.1)

    assert data["done_count"] == 1
    assert "M54629" in data["completed"]

    # Kiểm tra SSE stream endpoint
    r_stream = tc.get("/api/batch/events")
    assert r_stream.status_code == 200
    assert "text/event-stream" in r_stream.content_type

    r_stop = tc.post("/api/batch/stop")
    assert r_stop.status_code == 200
    assert r_stop.get_json()["ok"] is True


def test_batch_sse_stream_until_done(tmp_path):
    """SSE phải phát tiến độ trong lúc batch chạy và tự đóng stream khi batch kết thúc."""
    cfg = load_config()
    cfg["paths"] = {
        "input": str(tmp_path / "input"),
        "output": str(tmp_path / "output"),
        "work": str(tmp_path / "work"),
        "prompts": str(cfg.prompts_dir),
        "report": str(tmp_path / "output" / "baocao.xlsx"),
    }
    sample = cfg.root / "samples" / "M54629.docx"
    inp = tmp_path / "input" / "M54629.docx"
    inp.parent.mkdir(parents=True, exist_ok=True)
    inp.write_bytes(sample.read_bytes())

    app = create_app(cfg, mock=True)
    tc = app.test_client()
    assert tc.post("/api/batch/start", json={"stems": ["M54629"], "mock": True}).get_json()["ok"]

    # Stream tự kết thúc khi batch xong → get_data() trả về toàn bộ, không treo.
    r = tc.get("/api/batch/events")
    assert r.headers.get("Cache-Control") == "no-cache"
    events = [
        json.loads(line[len("data: "):])
        for line in r.get_data(as_text=True).splitlines()
        if line.startswith("data: ")
    ]
    assert events, "SSE không phát bản tin nào"
    assert events[-1]["active"] is False
    assert events[-1]["done_count"] == 1
    # Mỗi bản tin phát ra phải khác bản tin trước (không phát trùng)
    dumps = [json.dumps(e, sort_keys=True) for e in events]
    assert all(a != b for a, b in zip(dumps, dumps[1:]))


def test_check_update_endpoints(tmp_path, monkeypatch):
    monkeypatch.setattr("tomtat.updater.get_current_commit", lambda root=None: "aabbccddee")
    monkeypatch.setattr("tomtat.updater.get_remote_commit", lambda root=None: "aabbccddee")

    res = check_update(tmp_path)
    assert res["has_update"] is False
    assert res["status"] == "up_to_date"
    assert "Đã là phiên bản mới nhất" in res["message"]

    monkeypatch.setattr("tomtat.updater.get_remote_commit", lambda root=None: "1122334455")
    res2 = check_update(tmp_path)
    assert res2["has_update"] is True
    assert res2["status"] == "update_available"
    assert "Có bản cập nhật mới trên GitHub" in res2["message"]

    _save_version(tmp_path, "1122334455")
    v_data = json.loads((tmp_path / "version.json").read_text(encoding="utf-8"))
    assert v_data["commit"] == "1122334455"

    cfg = load_config()
    app = create_app(cfg)
    client = app.test_client()

    api_res = client.get("/api/check-update")
    assert api_res.status_code == 200
    data = api_res.get_json()
    assert data["has_update"] is True
    assert data["status"] == "update_available"


def test_phase1_quick_wins_refactor():
    from tomtat.suspicious import check_suspicious_document as susp_direct, BANK_KEYWORDS, VI_STOPWORDS
    from tomtat.checks import check_suspicious_document as susp_reexport
    assert susp_direct is susp_reexport
    assert isinstance(BANK_KEYWORDS, set)
    assert isinstance(VI_STOPWORDS, set)

    from tomtat.web.batch import BatchManager as BatchManagerDirect
    from tomtat.web import BatchManager as BatchManagerWeb
    from tomtat.web.app import BatchManager as BatchManagerApp
    assert BatchManagerDirect is BatchManagerWeb
    assert BatchManagerDirect is BatchManagerApp


def test_phase2_and_3_architecture_refactor(tmp_path):
    assert FileService is not None
    assert BatchManager is not None

    cfg = load_config()
    errors = cfg.validate()
    assert len(errors) == 0

    invalid_cfg = Config({"paths": {}})
    assert len(invalid_cfg.validate()) > 0

    from tomtat.llm import MockClient
    from tomtat.pipeline import Pipeline
    pipeline = Pipeline(cfg, MockClient(cfg))
    assert pipeline.cfg is cfg
    assert pipeline.renderer is not None

    cfg_tmp = load_config()
    cfg_tmp["paths"] = {
        "input": str(tmp_path / "input"),
        "output": str(tmp_path / "output"),
        "work": str(tmp_path / "work"),
        "prompts": str(cfg.prompts_dir),
        "report": str(tmp_path / "output" / "baocao.xlsx"),
    }
    svc = FileService(cfg_tmp)
    assert isinstance(svc.discover_files(), list)
    assert isinstance(svc.get_status_rows(), list)

    from tomtat.models import (
        CheckResult, DocSegment, DocumentBudget, FileState, Issue, LLMError, LLMResult,
        UnitItem, UnitsData,
    )
    assert CheckResult is not None
    assert Issue is not None
    assert LLMResult is not None
    assert LLMError is not None

    # TypedDict phải khớp đúng với dữ liệu thật mà code sinh ra (chống "models" lệch khỏi code)
    from tomtat.state import FileWorkspace
    from tomtat.units import inspect_docx
    units = inspect_docx(cfg.root / "samples" / "M54629.docx", cfg)
    assert set(units) == set(UnitsData.__annotations__)
    assert set(units["budget"]) == set(DocumentBudget.__annotations__)
    assert set(units["docs"][0]) == set(DocSegment.__annotations__)
    assert set(units["docs"][0]["units"][0]) == set(UnitItem.__annotations__)
    st_default = FileWorkspace(tmp_path / "w", tmp_path / "x.docx").default_state()
    assert set(st_default) <= set(FileState.__annotations__)

    # 6. Kiểm tra gọi song song generate_json_batch
    client = MockClient(cfg)
    calls = [{
        "step": "label",
        "system": "sys",
        "prompt": "prompt",
        "schema": {},
        "context": {"data": {"docs": [{"n": 1, "units": [{"text": "Điều 1.", "words": 2}], "words": 2}]}},
    }]
    batch_res = client.generate_json_batch(calls, max_workers=2)
    assert len(batch_res) == 1
    assert batch_res[0].model == "mock"


def test_phase4_es_modules_and_async_io(tmp_path):
    """Kiểm thử Phase 4: ES modules frontend và Async I/O (asyncio) trong LLM / Pipeline / FileService."""
    import asyncio
    from tomtat.llm import MockClient
    from tomtat.pipeline import Pipeline

    cfg = load_config()
    cfg["paths"] = {
        "input": str(tmp_path / "input"),
        "output": str(tmp_path / "output"),
        "work": str(tmp_path / "work"),
        "prompts": str(cfg.prompts_dir),
        "report": str(tmp_path / "output" / "baocao.xlsx"),
    }

    # 1. Kiểm tra index.html nạp app.js dưới dạng ES module và tất cả submodules đều truy cập được
    app = create_app(cfg, mock=True)
    tc = app.test_client()
    r_idx = tc.get("/")
    assert r_idx.status_code == 200
    assert 'type="module" src="/static/app.js"' in r_idx.get_data(as_text=True)

    for mod_name in ("state.js", "utils.js", "sidebar.js", "editor.js", "batch.js", "modals.js"):
        r_mod = tc.get(f"/static/modules/{mod_name}")
        assert r_mod.status_code == 200, f"Không tải được ES module {mod_name}"
        assert "export " in r_mod.get_data(as_text=True)

    # 2. Kiểm tra Async I/O: generate_json_async, generate_json_batch_async, Pipeline.process_batch_async
    sample = cfg.root / "samples" / "M54629.docx"
    inp = tmp_path / "input" / "M54629.docx"
    inp.parent.mkdir(parents=True, exist_ok=True)
    inp.write_bytes(sample.read_bytes())

    client = MockClient(cfg)
    call_spec = {
        "step": "label",
        "system": "sys",
        "prompt": "prompt",
        "schema": {},
        "context": {"data": {"docs": [{"n": 1, "units": [{"text": "Điều 1.", "words": 2}], "words": 2}]}},
    }

    async def _run_async_suite():
        single = await client.generate_json_async(**call_spec)
        assert single.model == "mock"

        batch = await client.generate_json_batch_async([call_spec, call_spec], max_concurrency=2)
        assert len(batch) == 2

        pipeline = Pipeline(cfg, client)
        pipe_res = await pipeline.process_batch_async([inp], max_concurrency=2, force=True)
        assert len(pipe_res) == 1
        assert pipe_res[0]["state"]["label"]["value"] != ""

        svc = FileService(cfg)
        svc_res = await svc.process_async(inp, client, only_step="inspect")
        assert svc_res["state"]["file"] == "M54629.docx"

    asyncio.run(_run_async_suite())


def test_realtime_logs_and_error_recovery_state_sync(tmp_path):
    """Kiểm thử:
    1. Log AI được ghi ngay khi cuộc gọi API bắt đầu (in_progress=True) và cập nhật cùng file khi xong (in_progress=False).
    2. Mỗi file chạy song song có thư mục log riêng và trạng thái current_step / updated_at riêng.
    3. File từng bị lỗi 429 ở bước abstract khi chạy lại sẽ xóa cờ lỗi ngay từ đầu và cập nhật sang da_co_nhap.
    """
    from tomtat.llm import MockClient
    from tomtat.pipeline import process_file
    from tomtat.state import STATUS_ERROR, FileWorkspace

    cfg = load_config()
    cfg["paths"] = {
        "input": str(tmp_path / "input"),
        "output": str(tmp_path / "output"),
        "work": str(tmp_path / "work"),
        "prompts": str(cfg.prompts_dir),
        "report": str(tmp_path / "output" / "baocao.xlsx"),
    }
    sample = cfg.root / "samples" / "M54629.docx"
    inp1 = tmp_path / "input" / "M51088.docx"
    inp2 = tmp_path / "input" / "M51089.docx"
    inp1.parent.mkdir(parents=True, exist_ok=True)
    inp1.write_bytes(sample.read_bytes())
    inp2.write_bytes(sample.read_bytes())

    # Giả lập M51088 đã xong label & extract nhưng bị lỗi 429 ở bước abstract
    fast_client = MockClient(cfg)
    process_file(inp1, cfg, fast_client, only_step="label")
    process_file(inp1, cfg, fast_client, only_step="extract")
    ws1 = FileWorkspace(cfg.work_dir, inp1)
    st1 = ws1.load_state()
    err_msg = (
        "Gọi Gemini thất bại ở bước abstract: RateLimitError: Error code: 429 - "
        "{'error': {'message': 'Your project has exceeded a quota.', 'code': 'too_many_requests'}}"
    )
    st1["status"] = STATUS_ERROR
    st1["error_message"] = err_msg
    st1["flags"].append(err_msg)
    ws1.save_state(st1)

    # Dùng MockClient có delay_s=0.35s để bắt được khoảnh khắc API đang gọi trực tiếp (in_progress=True)
    slow_client = MockClient(cfg, delay_s=0.35)
    app = create_app(cfg, mock=True)
    mgr: BatchManager = app.config["TOMTAT_BATCH_MGR"]
    mgr._client_factory = lambda _c, force_mock=False: slow_client
    tc = app.test_client()

    r_start = tc.post("/api/batch/start", json={"stems": ["M51088", "M51089"], "concurrency": 2, "mock": True})
    assert r_start.get_json()["ok"] is True

    saw_in_progress_m1 = False
    saw_in_progress_m2 = False
    saw_cleared_error_while_running = False

    for _ in range(40):
        r_l1 = tc.get("/api/file/M51088/logs").get_json()
        r_l2 = tc.get("/api/file/M51089/logs").get_json()
        if any(lg.get("in_progress") for lg in r_l1.get("logs", [])):
            saw_in_progress_m1 = True
        if any(lg.get("in_progress") for lg in r_l2.get("logs", [])):
            saw_in_progress_m2 = True

        f1_live = tc.get("/api/file/M51088").get_json()["state"]
        if all("RateLimitError" not in fl for fl in (f1_live.get("flags") or [])):
            saw_cleared_error_while_running = True

        st_batch = tc.get("/api/batch/status").get_json()
        if not st_batch["active"]:
            break
        time.sleep(0.08)

    # Chờ batch hoàn tất hẳn
    for _ in range(40):
        if not tc.get("/api/batch/status").get_json()["active"]:
            break
        time.sleep(0.1)

    assert saw_in_progress_m1 is True, "Phải thấy log in_progress=True của M51088 ngay trong lúc đang gọi API"
    assert saw_in_progress_m2 is True, "Phải thấy log in_progress=True của M51089 chạy song song"
    assert saw_cleared_error_while_running is True

    # Khi hoàn tất: không còn log nào bị treo in_progress, cờ lỗi 429 đã bị xóa sạch, trạng thái là da_co_nhap
    final_l1 = tc.get("/api/file/M51088/logs").get_json()["logs"]
    final_l2 = tc.get("/api/file/M51089/logs").get_json()["logs"]
    assert final_l1 and all(not lg["in_progress"] for lg in final_l1)
    assert final_l2 and all(not lg["in_progress"] for lg in final_l2)

    final_f1 = tc.get("/api/file/M51088").get_json()["state"]
    assert final_f1["status"] == "da_co_nhap"
    assert final_f1["error_message"] is None
    assert final_f1["current_step"] is None
    assert all("RateLimitError" not in fl for fl in (final_f1.get("flags") or []))
    assert final_f1["abstract"]["words"] > 0


def test_rerun_prevents_concurrent_same_file(tmp_path):
    """Không cho phép chạy lại AI (rerun) trùng trên cùng 1 file đang xử lý."""
    cfg = load_config()
    cfg["paths"] = {
        "input": str(tmp_path / "input"),
        "output": str(tmp_path / "output"),
        "work": str(tmp_path / "work"),
        "prompts": str(cfg.prompts_dir),
        "report": str(tmp_path / "output" / "baocao.xlsx"),
    }
    inp = tmp_path / "input" / "M54629.docx"
    inp.parent.mkdir(parents=True, exist_ok=True)
    inp.write_bytes((cfg.root / "samples" / "M54629.docx").read_bytes())

    app = create_app(cfg, mock=True)
    tc = app.test_client()
    mgr = app.config["TOMTAT_BATCH_MGR"]

    # 1. Khi file đang rerun đơn lẻ -> try_acquire_file lần 2 và API /rerun đều bị chặn (409)
    ok, msg = mgr.try_acquire_file("M54629", "abstract")
    assert ok is True and msg is None
    try:
        ok2, msg2 = mgr.try_acquire_file("M54629", "abstract")
        assert ok2 is False and msg2

        # /api/files, /api/batch/status và /api/file/<stem>/logs đều phải báo file đang chạy
        r_files = tc.get("/api/files").get_json()
        assert "M54629" in r_files["running_stems"]
        assert r_files["running_steps"].get("M54629") == "abstract"

        r_bst = tc.get("/api/batch/status").get_json()
        assert "M54629" in r_bst["rerun_running"]
        assert "M54629" in r_bst["all_running"]

        r_logs = tc.get("/api/file/M54629/logs").get_json()
        assert "M54629" in r_logs["running_stems"]

        r_rerun = tc.post("/api/file/M54629/rerun", json={"step": "abstract", "mock": True})
        assert r_rerun.status_code == 409
        body = r_rerun.get_json()
        assert body["already_running"] is True
    finally:
        mgr.release_file("M54629")

    # 2. Sau khi giải phóng khóa -> chạy lại bình thường
    ok3, _ = mgr.try_acquire_file("M54629", "label")
    assert ok3 is True
    mgr.release_file("M54629")

    # 3. Khi file đang nằm trong batch running_stems -> API /rerun cũng bị chặn (409)
    with mgr.lock:
        mgr.running_stems.add("M54629")
    try:
        r_rerun_batch = tc.post("/api/file/M54629/rerun", json={"step": "abstract", "mock": True})
        assert r_rerun_batch.status_code == 409
        assert r_rerun_batch.get_json()["already_running"] is True
    finally:
        with mgr.lock:
            mgr.running_stems.discard("M54629")


def test_get_file_logs_stale_threshold_respects_model_timeout(tmp_path):
    """Ngưỡng phát hiện log treo phải theo timeout thực tế của model (+30s đệm)
    và vẫn hiển thị đúng thời gian đã trôi qua thay vì 0s."""
    import json
    from tomtat.llm import DEFAULT_TIMEOUT_S, get_model_timeout_s
    from tomtat.services import FileService

    cfg = load_config()
    cfg["paths"] = {
        "input": str(tmp_path / "input"),
        "output": str(tmp_path / "output"),
        "work": str(tmp_path / "work"),
        "prompts": str(cfg.prompts_dir),
        "report": str(tmp_path / "output" / "baocao.xlsx"),
    }
    svc = FileService(cfg)
    ws = svc.get_workspace("M99999")
    ws.logs_dir.mkdir(parents=True, exist_ok=True)
    now = time.time()

    h_timeout = get_model_timeout_s("gemini-4-hydrogen", cfg) or DEFAULT_TIMEOUT_S["gemini-4-hydrogen"]
    f_timeout = get_model_timeout_s("gemini-3.8-flash", cfg) or DEFAULT_TIMEOUT_S["gemini-3.8-flash"]
    assert h_timeout >= 420.0 and f_timeout >= 240.0

    h_alive_age = h_timeout - 100.0
    h_stale_age = max(180.0, h_timeout + 30.0) + 10.0
    f_stale_age = max(180.0, f_timeout + 30.0) + 10.0

    # 1. gemini-4-hydrogen chưa vượt (timeout + 30s): vẫn đang chạy
    (ws.logs_dir / "001.json").write_text(
        json.dumps({
            "step": "abstract",
            "attempt": 1,
            "model": "gemini-4-hydrogen",
            "in_progress": True,
            "phase": "calling_api",
            "started_epoch": now - h_alive_age,
            "started_at": "2026-11-19T21:30:00",
        }),
        encoding="utf-8",
    )
    # 2. gemini-4-hydrogen vượt ngưỡng (timeout + 30s): mới báo lỗi treo và phải hiện đúng elapsed thay vì 0s
    (ws.logs_dir / "002.json").write_text(
        json.dumps({
            "step": "abstract",
            "attempt": 1,
            "model": "gemini-4-hydrogen",
            "in_progress": True,
            "phase": "calling_api",
            "started_epoch": now - h_stale_age,
            "started_at": "2026-11-19T21:25:40",
        }),
        encoding="utf-8",
    )
    # 3. gemini-3.8-flash vượt ngưỡng (timeout + 30s): báo lỗi treo và hiện đúng elapsed
    (ws.logs_dir / "003.json").write_text(
        json.dumps({
            "step": "extract",
            "attempt": 1,
            "model": "gemini-3.8-flash",
            "in_progress": True,
            "phase": "calling_api",
            "started_epoch": now - f_stale_age,
            "started_at": "2026-11-19T21:30:10",
        }),
        encoding="utf-8",
    )

    res = svc.get_file_logs("M99999")
    by_name = {lg["filename"]: lg for lg in res["logs"]}

    lg1 = by_name["001.json"]
    assert lg1["in_progress"] is True
    assert lg1["error"] == ""
    assert h_alive_age - 1.0 <= lg1["duration_s"] <= h_alive_age + 5.0

    lg2 = by_name["002.json"]
    assert lg2["in_progress"] is False
    assert lg2["error"] == "Quá thời gian chờ hoặc tiến trình cũ đã kết thúc"
    assert h_stale_age - 1.0 <= lg2["duration_s"] <= h_stale_age + 5.0

    lg3 = by_name["003.json"]
    assert lg3["in_progress"] is False
    assert lg3["error"] == "Quá thời gian chờ hoặc tiến trình cũ đã kết thúc"
    assert f_stale_age - 1.0 <= lg3["duration_s"] <= f_stale_age + 5.0


def test_api_get_logs_never_marks_actively_running_file_as_stale(tmp_path):
    """Khi file vẫn đang chạy trong BatchManager ở tiến trình server hiện tại,
    /api/file/<stem>/logs tuyệt đối không được báo nhầm 'Quá thời gian chờ hoặc tiến trình cũ đã kết thúc'."""
    import json
    from tomtat.services import FileService
    from tomtat.web import create_app

    cfg = load_config()
    cfg["paths"] = {
        "input": str(tmp_path / "input"),
        "output": str(tmp_path / "output"),
        "work": str(tmp_path / "work"),
        "prompts": str(cfg.prompts_dir),
        "report": str(tmp_path / "output" / "baocao.xlsx"),
    }
    svc = FileService(cfg)
    ws = svc.get_workspace("S95522")
    ws.logs_dir.mkdir(parents=True, exist_ok=True)
    now = time.time()

    # Log bắt đầu sau khi server khởi động và đã chạy 350s (hoặc 470s) trong khi BatchManager vẫn giữ stem
    (ws.logs_dir / "live.json").write_text(
        json.dumps({
            "step": "abstract",
            "attempt": 1,
            "model": "gemini-4-hydrogen",
            "in_progress": True,
            "phase": "calling_api",
            "started_epoch": now - 470.0,
            "started_at": "2026-11-19T23:54:51",
        }),
        encoding="utf-8",
    )

    app = create_app(cfg, mock=True)
    app.config["TOMTAT_STARTED_EPOCH"] = now - 600.0
    app.config["TOMTAT_BATCH_MGR"].running_stems.add("S95522")
    tc = app.test_client()

    data = tc.get("/api/file/S95522/logs").get_json()
    assert len(data["logs"]) == 1
    assert data["logs"][0]["in_progress"] is True
    assert data["logs"][0]["error"] == ""

    # Nếu log bắt đầu TRƯỚC khi server khởi động lại (tiến trình cũ đã chết), vẫn phải đánh dấu lỗi treo
    app.config["TOMTAT_STARTED_EPOCH"] = now - 10.0
    data_old = tc.get("/api/file/S95522/logs").get_json()
    assert data_old["logs"][0]["in_progress"] is False
    assert data_old["logs"][0]["error"] == "Quá thời gian chờ hoặc tiến trình cũ đã kết thúc"





