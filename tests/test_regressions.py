"""Bộ kiểm thử hồi quy (Regression Tests) khóa cứng các lỗi đã fix để không tái diễn."""
from __future__ import annotations

import importlib.util
import io
import re
import zipfile

from tomtat.config import PROJECT_ROOT, get_api_keys, load_config
from tomtat.llm import GeminiClient
from tomtat.updater import PROTECTED_ENTRIES, check_update, run_update


def test_bug_002_api_keys_txt_overrides_env(tmp_path, monkeypatch):
    """BUG-002: api_keys.txt phải ưu tiên tuyệt đối so với key cũ còn sót trong .env / GEMINI_API_KEY."""
    import tomtat.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "legacy_env_key_should_be_ignored")
    monkeypatch.setenv("GEMINI_API_KEYS", "legacy_env_key_1,legacy_env_key_2")
    monkeypatch.setenv("GEMINI_API_KEY_1", "legacy_env_numbered_key")

    (tmp_path / "api_keys.txt").write_text(
        "# Danh sach key moi\nfile_key_1\n\nfile_key_2\n",
        encoding="utf-8",
    )

    keys = get_api_keys()
    assert keys == ["file_key_1", "file_key_2"]


def test_bug_002_401_403_invalid_key_exhausts_all_models(tmp_path, monkeypatch):
    """BUG-002: Gặp 401/403 phải khóa ngay key lỗi trên toàn bộ models_chain và chuyển sang key kế tiếp."""
    monkeypatch.setattr("tomtat.llm._get_quota_cycle_date", lambda: "2026-01-01")
    keys = ["bad_key", "good_key"]
    monkeypatch.setattr("tomtat.config.get_api_keys", lambda root=None: keys)

    cfg = load_config()
    cfg["paths"] = {"work": str(tmp_path / "work")}
    cfg["gemini"]["min_interval_s"] = 0.0
    cfg["gemini"]["backoff_base_s"] = 0.0
    cfg["gemini"]["backoff_max_s"] = 0.0

    client = GeminiClient(cfg, keys)
    client_map: dict[str, object] = {}

    class Err401(Exception):
        status_code = 401

    def fake_get(k: str):
        return client_map.setdefault(k, object())

    def fake_call_once(model_name, step, system, prompt, schema, client=None):
        k = next(key for key, obj in client_map.items() if obj is client)
        if k == "bad_key":
            raise Err401("API key not valid")
        return '{"ok": true}', {}, "completed"

    monkeypatch.setattr(client, "_get_or_create_client", fake_get)
    monkeypatch.setattr(client, "_call_once", fake_call_once)

    res = client.generate_json(step="label", system="", prompt="", schema={})
    assert res.data == {"ok": True}
    for m in client.models_chain:
        assert ("bad_key", m) in client._exhausted
        assert ("good_key", m) not in client._exhausted


def test_bug_004_config_models_valid_and_consistent():
    """BUG-004: config.yaml không được chứa model khai tử (2.5/3.7/3.5-pro) và phải đủ hạn mức cho cả chain."""
    cfg = load_config()
    cfg.validate(raise_on_error=True)

    g = cfg["gemini"]
    chain = [g["model"]] + list(g.get("fallback_models") or [])
    assert len(chain) >= 2

    banned = ("gemini-2.5", "gemini-3.7", "gemini-3.5-pro")
    for model_name in chain:
        assert not any(model_name.startswith(b) for b in banned), f"Invalid/deprecated model: {model_name}"
        assert model_name in (g.get("rpd_limits") or {}), f"Missing rpd_limits for {model_name}"
        assert model_name in (g.get("rpm_limits") or {}), f"Missing rpm_limits for {model_name}"
        assert model_name in (g.get("min_interval_s") or {}), f"Missing min_interval_s for {model_name}"


def test_bug_005_updater_zip_protects_user_entries(tmp_path, monkeypatch):
    """BUG-005: Cập nhật qua ZIP (máy không có Git) phải giữ nguyên 100% PROTECTED_ENTRIES của người dùng."""
    # Tạo dữ liệu người dùng cần được bảo vệ
    (tmp_path / "input").mkdir()
    (tmp_path / "input" / "user_doc.docx").write_bytes(b"user-input")
    (tmp_path / "output").mkdir()
    (tmp_path / "output" / "done.docx").write_bytes(b"user-output")
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "state.json").write_text('{"keep": true}', encoding="utf-8")
    (tmp_path / ".venv" / "Scripts").mkdir(parents=True)
    (tmp_path / ".venv" / "Scripts" / "python.exe").write_bytes(b"venv")
    (tmp_path / ".env").write_text("SECRET=user", encoding="utf-8")
    (tmp_path / "api_keys.txt").write_text("user_real_key\n", encoding="utf-8")
    (tmp_path / ".github_repo_url").write_text("https://github.com/Bowfm/TomTatBienSoan.git", encoding="utf-8")

    # Tạo archive ZIP giả lập bản mới từ GitHub có chứa cả file trùng tên trong PROTECTED_ENTRIES
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.comment = b"abcdef1234567890"
        zf.writestr("TomTatBienSoan-main/README.md", "updated readme")
        zf.writestr("TomTatBienSoan-main/input/user_doc.docx", "OVERWRITTEN")
        zf.writestr("TomTatBienSoan-main/output/done.docx", "OVERWRITTEN")
        zf.writestr("TomTatBienSoan-main/work/state.json", "OVERWRITTEN")
        zf.writestr("TomTatBienSoan-main/.venv/Scripts/python.exe", "OVERWRITTEN")
        zf.writestr("TomTatBienSoan-main/.env", "OVERWRITTEN")
        zf.writestr("TomTatBienSoan-main/api_keys.txt", "OVERWRITTEN")
    zip_data = buf.getvalue()

    class FakeResp:
        def __init__(self, data: bytes):
            self._data = data

        def read(self) -> bytes:
            return self._data

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=30, context=None: FakeResp(zip_data))

    ok, _msg = run_update(tmp_path)
    assert ok is True
    assert PROTECTED_ENTRIES == {"input", "output", "work", ".venv", ".env", "api_keys.txt"}
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "updated readme"
    assert (tmp_path / "input" / "user_doc.docx").read_bytes() == b"user-input"
    assert (tmp_path / "output" / "done.docx").read_bytes() == b"user-output"
    assert (tmp_path / "work" / "state.json").read_text(encoding="utf-8") == '{"keep": true}'
    assert (tmp_path / ".venv" / "Scripts" / "python.exe").read_bytes() == b"venv"
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "SECRET=user"
    assert (tmp_path / "api_keys.txt").read_text(encoding="utf-8") == "user_real_key\n"


def test_bug_007_setup_bat_avoids_nested_paren_blocks_and_checks_paths():
    """BUG-007: setup.bat không được dùng khối `if (...)` bọc logic dò Python đa đường dẫn gây văng cmd.exe."""
    setup_text = (PROJECT_ROOT / "setup.bat").read_text(encoding="utf-8")

    # Vẫn giữ bước chống chạy trực tiếp bên trong ZIP chưa giải nén
    assert 'if exist "%~dp0requirements.txt" goto :has_requirements' in setup_text
    # Logic dò Python dùng goto nhãn tuyến tính thay vì lồng khối dấu ngoặc
    assert ":check_python_cmd" in setup_text
    assert ":check_default_paths" in setup_text
    assert not re.search(r"where\s+py\s+.*\n\s*if\s+not\s+errorlevel\s+1\s*\(", setup_text, re.IGNORECASE)


def test_bug_008_khoi_dong_bat_non_blocking_startup():
    """BUG-008: khoi_dong.bat không được chạy run_update() đồng bộ chặn lúc khởi động Web UI."""
    kd_text = (PROJECT_ROOT / "khoi_dong.bat").read_text(encoding="utf-8")
    assert "run_update(" not in kd_text
    assert "check_update(" in kd_text
    assert "tomtat.bat" in kd_text


def test_bug_009_updater_does_not_report_update_when_local_ahead(tmp_path, monkeypatch):
    """BUG-009: Khi commit local mới hơn GitHub (remote là tổ tiên của HEAD), check_update phải báo up_to_date."""
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr("tomtat.updater.get_current_commit", lambda root=None: "2222222bbbbbbb")
    monkeypatch.setattr("tomtat.updater.get_remote_commit", lambda root=None: "1111111aaaaaaa")
    monkeypatch.setattr(
        "tomtat.updater._is_git_ancestor",
        lambda root, ancestor, descendant: ancestor == "1111111aaaaaaa" and descendant == "2222222bbbbbbb",
    )

    res = check_update(tmp_path)
    assert res["has_update"] is False
    assert res["status"] == "up_to_date"
    assert "mới hơn GitHub" in res["message"]


def test_bug_013_frontend_es_modules_syntax_and_imports():
    """BUG-013: Toàn bộ JS ES modules phải hợp lệ cú pháp và mọi import { sym } phải khớp export đích."""
    guard_path = PROJECT_ROOT / ".agents" / "verify_guard.py"
    spec = importlib.util.spec_from_file_location("verify_guard", guard_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    errors = mod.check_frontend_js()
    assert errors == [], "\n".join(errors)



# ---------------------------------------------------------------------------
# BUG-014: Xử lý song song 2 file — không bao giờ vượt RPM/key và luôn tôn trọng trần max_workers
# ---------------------------------------------------------------------------

def test_bug_014_parallel_acquire_never_exceeds_per_key_rpm(tmp_path, monkeypatch):
    """BUG-014: 2 luồng cùng đặt chỗ key cho 1 model không được làm (key, model) vượt min_interval_s / RPM."""
    import threading

    from tomtat.llm.base import get_model_min_interval_s, get_model_rpm_per_key

    monkeypatch.setattr("tomtat.llm._get_quota_cycle_date", lambda: "2026-01-01")
    keys = ["k1", "k2", "k3"]
    monkeypatch.setattr("tomtat.config.get_api_keys", lambda root=None: keys)
    cfg = load_config()
    cfg["paths"] = {"work": str(tmp_path / "work")}
    client = GeminiClient(cfg, keys)
    monkeypatch.setattr(client, "_get_or_create_client", lambda k: object())
    monkeypatch.setattr(client, "_save_quota_state_unlocked", lambda: None)

    for model in ("gemini-4-hydrogen", "gemini-3.5-flash-lite"):
        reserved: dict[str, list[float]] = {k: [] for k in keys}
        lock = threading.Lock()
        barrier = threading.Barrier(2)

        def worker():
            barrier.wait()
            for _ in range(60):
                k, _c, _w, ts = client._acquire_paced_key_client_with_ts(model)
                assert k is not None
                with lock:
                    reserved[k].append(ts)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        min_iv = get_model_min_interval_s(model, cfg)
        rpm = get_model_rpm_per_key(model, cfg)
        assert sum(len(v) for v in reserved.values()) == 120
        for k, ts_list in reserved.items():
            ts_list.sort()
            assert ts_list, f"{model}: key {k} không được dùng (cursor không xoay vòng)"
            for a, b in zip(ts_list, ts_list[1:]):
                assert b - a >= min_iv - 1e-6, f"{model}/{k}: 2 lượt cách nhau {b - a:.2f}s < {min_iv}s"
            for i, t0 in enumerate(ts_list):
                in_window = sum(1 for t in ts_list[i:] if t < t0 + 60.0)
                assert in_window <= rpm, f"{model}/{k}: {in_window} lượt trong 60s > RPM {rpm}"


def _make_batch_cfg(tmp_path, monkeypatch, max_workers):
    monkeypatch.setattr("tomtat.config.get_api_keys", lambda root=None: [f"key_{i}" for i in range(48)])
    cfg = load_config()
    cfg["paths"] = {
        "input": str(tmp_path / "input"),
        "output": str(tmp_path / "output"),
        "work": str(tmp_path / "work"),
        "prompts": str(cfg.prompts_dir),
        "report": str(tmp_path / "output" / "baocao.xlsx"),
    }
    cfg["workers"] = 2
    if max_workers is None:
        cfg.pop("max_workers", None)
    else:
        cfg["max_workers"] = max_workers
    monkeypatch.setattr(cfg, "reload", lambda: None)  # giữ nguyên cấu hình test, không nạp lại từ đĩa
    return cfg


def _run_batch_and_measure(cfg, monkeypatch, stems, concurrency):
    import threading
    import time as _time

    import tomtat.services.batch_service as bs

    state = {"cur": 0, "peak": 0}
    lock = threading.Lock()

    def fake_process_file(docx_path, cfg_, client, **kw):
        with lock:
            state["cur"] += 1
            state["peak"] = max(state["peak"], state["cur"])
        _time.sleep(0.15)
        with lock:
            state["cur"] -= 1

    monkeypatch.setattr(bs, "process_file", fake_process_file)
    monkeypatch.setattr(bs, "write_reports", lambda c: None)
    mgr = bs.BatchManager(
        cfg,
        client_factory=lambda _c, force_mock=False: object(),
        files_resolver=lambda _c: {},
    )
    res = mgr.start(stems, concurrency=concurrency)
    assert res["ok"], res
    workers = mgr.executor._max_workers
    deadline = _time.time() + 10
    while mgr.status()["active"] and _time.time() < deadline:
        _time.sleep(0.02)
    st = mgr.status()
    assert not st["active"]
    assert st["done_count"] == len(stems) and st["err_count"] == 0
    return workers, state["peak"]


def test_bug_014_batch_runs_exactly_two_files_concurrently(tmp_path, monkeypatch):
    """BUG-014: Batch mặc định (workers=2) xử lý đúng 2 file cùng lúc, không bao giờ hơn."""
    cfg = _make_batch_cfg(tmp_path, monkeypatch, max_workers=2)
    workers, peak = _run_batch_and_measure(cfg, monkeypatch, [f"S{i}" for i in range(5)], "auto")
    assert workers == 2
    assert peak == 2


def test_bug_014_max_workers_caps_ui_api_and_scaling(tmp_path, monkeypatch):
    """BUG-014: Trần max_workers phải chặn get_pool_scaling (UI), BatchManager (API) và clamp_workers (CLI)."""
    from tomtat.config import clamp_workers, get_pool_scaling

    cfg = _make_batch_cfg(tmp_path, monkeypatch, max_workers=2)
    cfg["workers"] = "auto"  # 48 key -> công thức auto ra 5 luồng, vẫn phải bị kẹp về 2
    sc = get_pool_scaling(cfg)
    assert sc["max_workers"] == 2
    assert sc["recommended_workers"] == 2
    assert sc["concurrency_options"] and max(sc["concurrency_options"]) <= 2
    assert sc["max_concurrent_calls"] >= sc["recommended_workers"]
    assert clamp_workers(16, cfg) == 2
    assert clamp_workers(None, cfg) == 2
    assert clamp_workers(1, cfg) == 1

    # Gọi API trực tiếp với concurrency=16 vẫn chỉ chạy 2 luồng
    workers, peak = _run_batch_and_measure(cfg, monkeypatch, [f"M{i}" for i in range(6)], 16)
    assert workers == 2
    assert peak <= 2

    # Không cấu hình trần -> giữ hành vi cũ (tôn trọng số luồng người dùng chọn)
    cfg_nocap = _make_batch_cfg(tmp_path, monkeypatch, max_workers=None)
    assert get_pool_scaling(cfg_nocap)["max_workers"] is None
    assert clamp_workers(4, cfg_nocap) == 4


def test_bug_014_config_concurrency_contract():
    """BUG-014: config.yaml phải có 1 <= workers <= max_workers và min_interval_s đủ giãn để không chạm trần RPM."""
    from tomtat.llm.base import get_model_min_interval_s, get_model_rpm_per_key

    cfg = load_config()
    w, cap = cfg.get("workers"), cfg.get("max_workers")
    assert isinstance(cap, int) and not isinstance(cap, bool) and cap >= 1
    assert isinstance(w, int) and 1 <= w <= cap
    chain = [cfg["gemini"]["model"]] + list(cfg["gemini"].get("fallback_models") or [])
    for m in chain:
        iv = get_model_min_interval_s(m, cfg)
        rpm = get_model_rpm_per_key(m, cfg)
        assert iv > 0 and 60.0 / iv < rpm, f"{m}: 60/{iv} = {60.0 / iv:.2f} RPM >= trần {rpm}"

    # Giao diện không được viết cứng mức luồng vượt trần
    html = (PROJECT_ROOT / "src" / "tomtat" / "web" / "templates" / "index.html").read_text(encoding="utf-8")
    sel = re.search(r'<select id="sel-concurrency".*?</select>', html, re.S)
    assert sel is not None
    vals = [int(v) for v in re.findall(r'<option value="(\d+)"', sel.group(0))]
    assert vals and max(vals) <= cap
    js = (PROJECT_ROOT / "src" / "tomtat" / "web" / "static" / "modules" / "sidebar.js").read_text(encoding="utf-8")
    assert "max_workers" in js



def test_bug_014_api_batch_start_respects_reload_and_cap(tmp_path, monkeypatch):
    """BUG-014: Endpoint /api/batch/start với 'auto' phải dùng workers sau khi reload config và vẫn kẹp bởi max_workers."""
    import time as _time
    import tomtat.services.batch_service as bs
    from tomtat.web import create_app

    cfg = _make_batch_cfg(tmp_path, monkeypatch, max_workers=2)
    cfg["workers"] = 1  # giá trị cũ trước khi reload

    def fake_reload():
        cfg["workers"] = 2
        cfg["max_workers"] = 2

    monkeypatch.setattr(cfg, "reload", fake_reload)
    monkeypatch.setattr(bs, "process_file", lambda *a, **kw: _time.sleep(0.05))
    monkeypatch.setattr(bs, "write_reports", lambda c: None)

    app = create_app(cfg, mock=True)
    mgr = app.config["TOMTAT_BATCH_MGR"]
    mgr._client_factory = lambda _c, force_mock=False: object()
    tc = app.test_client()

    # Gửi concurrency="auto": không được chốt thành 1 trước khi reload(), phải chạy 2 luồng
    r1 = tc.post("/api/batch/start", json={"stems": ["F1", "F2", "F3"], "concurrency": "auto", "mock": True})
    assert r1.get_json()["ok"] is True
    assert mgr.executor._max_workers == 2
    deadline = _time.time() + 5
    while mgr.status()["active"] and _time.time() < deadline:
        _time.sleep(0.02)

    # Gửi concurrency=16 qua HTTP: vẫn phải bị kẹp về 2
    r2 = tc.post("/api/batch/start", json={"stems": ["F1", "F2", "F3"], "concurrency": 16, "mock": True})
    assert r2.get_json()["ok"] is True
    assert mgr.executor._max_workers == 2
    deadline = _time.time() + 5
    while mgr.status()["active"] and _time.time() < deadline:
        _time.sleep(0.02)


def test_bug_014_out_of_order_timestamp_update_keeps_rpm_sorted(tmp_path, monkeypatch):
    """BUG-014: Cập nhật actual_ts trễ hơn slot sau không làm đảo thứ tự cửa sổ trượt RPM."""
    monkeypatch.setattr("tomtat.llm._get_quota_cycle_date", lambda: "2026-01-01")
    keys = ["k1"]
    monkeypatch.setattr("tomtat.config.get_api_keys", lambda root=None: keys)
    cfg = load_config()
    cfg["paths"] = {"work": str(tmp_path / "work")}
    client = GeminiClient(cfg, keys)
    monkeypatch.setattr(client, "_save_quota_state_unlocked", lambda: None)

    model = "gemini-4-hydrogen"  # min_interval_s = 13.0, rpm = 5
    pair = ("k1", model)
    now = 1000.0
    client._model_rpm_tracker[pair] = [now, now + 13.0]
    client._rpm_tracker["k1"] = [now, now + 13.0]
    client._next_allowed_ts[pair] = now + 26.0

    # Slot đầu (now) thực tế bị trễ tới now + 30.0 (> slot thứ hai now + 13.0)
    client._update_call_timestamp("k1", model, now, now + 30.0)
    assert client._model_rpm_tracker[pair] == [now + 13.0, now + 30.0]

    # Ở thời điểm now + 35.0, phải chờ ít nhất tới (now + 30.0) + 13.0 = now + 43.0
    ready = client._earliest_ready_ts_unlocked("k1", model, now + 35.0)
    assert ready >= now + 43.0 - 1e-6


# ---------------------------------------------------------------------------
# BUG-015: "AI: Chạy lại cả bài" (step="all") bị skip sạch không gọi LLM và không ghi log mới
# ---------------------------------------------------------------------------

def test_bug_015_rerun_all_actually_reruns_llm_and_writes_logs(tmp_path, monkeypatch):
    """BUG-015: Khi file đã có nháp hợp lệ, POST /api/file/<stem>/rerun với step='all'
    phải chạy lại từ đầu (inspect -> label -> extract -> abstract), ghi log mới và xóa stale_steps cũ."""
    from tomtat.llm import MockClient
    from tomtat.pipeline import process_file
    from tomtat.state import FileWorkspace
    from tomtat.web import create_app

    cfg = load_config()
    cfg["paths"] = {
        "input": str(tmp_path / "input"),
        "output": str(tmp_path / "output"),
        "work": str(tmp_path / "work"),
        "prompts": str(cfg.prompts_dir),
        "report": str(tmp_path / "output" / "baocao.xlsx"),
    }
    cfg["judge"]["enabled"] = False
    monkeypatch.setattr(cfg, "reload", lambda: None)

    inp = tmp_path / "input" / "M54629.docx"
    inp.parent.mkdir(parents=True, exist_ok=True)
    inp.write_bytes((cfg.root / "samples" / "M54629.docx").read_bytes())

    # 1. Chạy lần đầu để file đạt trạng thái 'da_co_nhap' và giả lập từng có stale_steps + judge cũ
    first_client = MockClient(cfg)
    first_out = process_file(inp, cfg, first_client, force=True)
    assert first_out["state"]["status"] == "da_co_nhap"

    ws = FileWorkspace(cfg.work_dir, inp)
    st_before = ws.load_state()
    st_before["stale_steps"] = ["judge"]
    st_before["judge"] = {"average": 3.5, "relevance": 3.5, "coherence": 3.5, "consistency": 3.5, "fluency": 3.5}
    ws.save_state(st_before)

    logs_before = sorted(ws.logs_dir.glob("*.json"))
    assert len(logs_before) >= 3

    # 2. Gọi /api/file/M54629/rerun với step='all' (kể cả khi KHÔNG truyền restart từ client cũ)
    spy_client = MockClient(cfg)
    monkeypatch.setattr("tomtat.web.routes.process.make_client", lambda c, force_mock=False: spy_client)

    app = create_app(cfg, mock=True)
    tc = app.test_client()
    r = tc.post("/api/file/M54629/rerun", json={"step": "all", "mock": True, "ignore_mismatch": True})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True

    # Phải thực sự gọi lại đủ 3 bước AI và sinh thêm file log mới
    assert "label" in spy_client.calls
    assert "extract" in spy_client.calls
    assert "abstract" in spy_client.calls
    logs_after = sorted(ws.logs_dir.glob("*.json"))
    assert len(logs_after) >= len(logs_before) + 3

    # State sau khi chạy lại cả bài phải sạch stale_steps và judge cũ (khi judge.enabled=False)
    st_after = body["state"]
    assert st_after["status"] == "da_co_nhap"
    assert st_after["stale_steps"] == []
    assert st_after["judge"] is None

    # 3. Frontend editor.js phải gửi tường minh restart: step === "all"
    editor_js = (PROJECT_ROOT / "src" / "tomtat" / "web" / "static" / "modules" / "editor.js").read_text(encoding="utf-8")
    assert 'restart: step === "all"' in editor_js

    # 4. Nếu caller truyền tường minh restart: false thì vẫn giữ chế độ kế thừa thông minh (0 lần gọi thêm)
    resume_client = MockClient(cfg)
    monkeypatch.setattr("tomtat.web.routes.process.make_client", lambda c, force_mock=False: resume_client)
    r_resume = tc.post("/api/file/M54629/rerun", json={"step": "all", "restart": False, "mock": True})
    assert r_resume.status_code == 200
    assert resume_client.calls == []


def test_bug_015_rerun_all_failure_does_not_overwrite_saved_label(tmp_path, monkeypatch):
    """BUG-015: Nếu chạy lại cả bài (hoặc gán nhãn lại) thất bại trước khi gọi AI (vd thiếu API key),
    không được ghi đè trước dropdown style trên màn hình vào state.json."""
    from tomtat.llm import MockClient
    from tomtat.pipeline import process_file
    from tomtat.state import FileWorkspace
    from tomtat.web import create_app

    cfg = load_config()
    cfg["paths"] = {
        "input": str(tmp_path / "input"),
        "output": str(tmp_path / "output"),
        "work": str(tmp_path / "work"),
        "prompts": str(cfg.prompts_dir),
        "report": str(tmp_path / "output" / "baocao.xlsx"),
    }
    monkeypatch.setattr(cfg, "reload", lambda: None)

    inp = tmp_path / "input" / "M54629.docx"
    inp.parent.mkdir(parents=True, exist_ok=True)
    inp.write_bytes((cfg.root / "samples" / "M54629.docx").read_bytes())

    process_file(inp, cfg, MockClient(cfg), force=True)
    ws = FileWorkspace(cfg.work_dir, inp)
    orig_style = ws.load_state()["label"]["value"]
    alt_style = "chính luận" if orig_style != "chính luận" else "khoa học"

    def fail_client(_c, force_mock=False):
        raise RuntimeError("Thiếu API key")

    monkeypatch.setattr("tomtat.web.routes.process.make_client", fail_client)
    app = create_app(cfg, mock=False)
    tc = app.test_client()
    r = tc.post("/api/file/M54629/rerun", json={"step": "all", "style": alt_style, "mock": False})
    assert r.status_code == 400
    assert ws.load_state()["label"]["value"] == orig_style


# ---------------------------------------------------------------------------
# BUG-016: Lỗi 429 QPS payload trùng lặp khóa oan toàn bộ model fallback (3.8/3.6/lite) & sai net_try #1
# ---------------------------------------------------------------------------

def test_bug_016_payload_qps_429_does_not_exhaust_keys_and_falls_back_cleanly(tmp_path, monkeypatch):
    """BUG-016: Lỗi 429 'query-per-second (QPS) limit for identical or near-identical request payloads'
    tuyệt đối không được tính strike RPD hay khóa key, không spam hết toàn bộ key rồi mới chuyển model,
    và khi model chính hết RPD thật phải trượt xuống 3.8/3.6 bình thường."""
    import pytest
    from tomtat.llm import LLMError

    monkeypatch.setattr("tomtat.llm._get_quota_cycle_date", lambda: "2026-01-01")
    monkeypatch.setattr("tomtat.llm.gemini._COOLDOWN_S", 0.0)
    monkeypatch.setattr("tomtat.llm.base.get_model_rpm_per_key", lambda m, c=None: 10_000)
    monkeypatch.setattr("tomtat.llm.gemini.get_model_rpm_per_key", lambda m, c=None: 10_000)

    keys = [f"key_test_{i:04d}" for i in range(1, 7)]
    monkeypatch.setattr("tomtat.config.get_api_keys", lambda root=None: keys)

    cfg = load_config()
    cfg["paths"] = {"work": str(tmp_path / "work")}
    cfg["gemini"]["min_interval_s"] = 0.0
    cfg["gemini"]["rpm_limits"] = {}
    cfg["gemini"]["backoff_base_s"] = 0.0
    cfg["gemini"]["backoff_max_s"] = 0.0

    client = GeminiClient(cfg, keys)
    client_map: dict[str, object] = {}
    calls: list[tuple[str, str]] = []
    outcomes: dict[tuple[str, str], list[str]] = {}

    class _Err429(Exception):
        status_code = 429

    def fake_get(k: str):
        return client_map.setdefault(k, object())

    def fake_call_once(model_name, step, system, prompt, schema, client=None):
        k = next(key for key, obj in client_map.items() if obj is client)
        calls.append((model_name, k))
        q = outcomes.get((model_name, k))
        act = q.pop(0) if q else "ok"
        if act == "429_rpd":
            raise _Err429(
                f"Error code: 429 - {{'error': {{'message': 'Rate limit exceeded for model {model_name} "
                f"(limit: 20 requests per day on Free Tier). Please retry in 11h32m12s.', 'code': 'too_many_requests'}}}}"
            )
        if act == "429_qps":
            raise _Err429(
                "Error code: 429 - {'error': {'message': 'The query-per-second (QPS) limit for identical or "
                "near-identical request payloads has been exceeded. See https://ai.dev/rate-limit.', 'code': 'too_many_requests'}}"
            )
        return '{"ok": true}', {}, "completed"

    monkeypatch.setattr(client, "_get_or_create_client", fake_get)
    monkeypatch.setattr(client, "_call_once", fake_call_once)

    H = "gemini-4-hydrogen"
    F38 = "gemini-3.8-flash"
    F36 = "gemini-3.6-flash"
    log_dir = tmp_path / "work" / "S95587" / "logs"

    # Trường hợp 1: Mọi key của gemini-4-hydrogen hết RPD thật; trên 3.8 gặp 2 lần 429 QPS rồi key kế tiếp thành công
    for k in keys:
        outcomes[(H, k)] = ["429_rpd", "429_rpd", "429_rpd"]
    outcomes[(F38, "key_test_0001")] = ["429_qps"]
    outcomes[(F38, "key_test_0002")] = ["429_qps"]
    outcomes[(F38, "key_test_0003")] = ["ok"]

    res = client.generate_json(step="extract", system="sys", prompt="same prompt", schema={}, log_dir=log_dir, attempt=1)
    assert res.model == F38
    assert all((k, H) in client._exhausted for k in keys)
    # Key 1 và Key 2 của 3.8 KHÔNG bị strike và KHÔNG bị exhausted
    assert ("key_test_0001", F38) not in client._exhausted
    assert ("key_test_0002", F38) not in client._exhausted
    assert client._idle_429_strikes.get(("key_test_0001", F38), 0) == 0
    assert client._idle_429_strikes.get(("key_test_0002", F38), 0) == 0

    # Kiểm tra riêng với _COOLDOWN_S = 30.0: 429 QPS KHÔNG đặt cooldown 30s lên key
    monkeypatch.setattr("tomtat.llm.gemini._COOLDOWN_S", 30.0)
    client._key_cooldown.clear()
    outcomes[(F38, "key_test_0004")] = ["429_qps"]
    outcomes[(F38, "key_test_0005")] = ["ok"]
    res2 = client.generate_json(step="extract", system="sys", prompt="another prompt", schema={}, log_dir=log_dir, attempt=1)
    assert res2.model == F38
    assert client._key_cooldown.get("key_test_0004", 0.0) == 0.0

    # Log cho thấy hành động backoff_payload_qps thay vì mark_exhausted_rpd (2 lần từ res + 1 lần từ res2)
    import json
    log_items = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(log_dir.glob("*.json"))]
    qps_logs = [d for d in log_items if d.get("model") == F38 and d.get("action") == "backoff_payload_qps"]
    assert len(qps_logs) == 3
    assert all("strikes_429" not in d for d in qps_logs)

    # Trường hợp 2: Nếu tất cả các model đều bị 429 QPS liên tục, chỉ thử tối đa 3 lần QPS/model rồi chuyển model,
    # tuyệt đối không khóa bất kỳ key nào của 3.8 / 3.6 / lite và thông báo lỗi phân biệt rõ QPS với RPD
    calls.clear()
    outcomes.clear()
    for m in (F38, F36, "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"):
        for k in keys:
            outcomes[(m, k)] = ["429_qps"] * 10

    with pytest.raises(LLMError) as exc_info:
        client.generate_json(step="extract", system="sys", prompt="same prompt", schema={})
    assert "QPS" in str(exc_info.value)
    assert "hết RPD trên mọi model" not in str(exc_info.value)
    for m in (F38, F36, "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"):
        m_calls = [k for mod, k in calls if mod == m]
        assert len(m_calls) == 3  # không spam cả pool x 3 vòng = 18 lần
        assert all((k, m) not in client._exhausted for k in keys)


def test_bug_016_qps_backoff_schedules_increasing_wait_and_payload_pacing(tmp_path, monkeypatch):
    """BUG-016: Khi backoff_base > 0, QPS backoff tăng dần và giữ khoảng cách tối thiểu giữa payload giống nhau."""
    monkeypatch.setattr("tomtat.llm._get_quota_cycle_date", lambda: "2026-01-01")
    keys = ["key_test_0001", "key_test_0002"]
    monkeypatch.setattr("tomtat.config.get_api_keys", lambda root=None: keys)

    cfg = load_config()
    cfg["paths"] = {"work": str(tmp_path / "work")}
    cfg["gemini"]["backoff_base_s"] = 0.05
    cfg["gemini"]["backoff_max_s"] = 0.25

    client = GeminiClient(cfg, keys)
    b1 = client._schedule_payload_qps_backoff("sig_abc", 1)
    b2 = client._schedule_payload_qps_backoff("sig_abc", 2)
    b3 = client._schedule_payload_qps_backoff("sig_abc", 3)
    assert 0.04 <= b1 < b2 < b3 <= 0.25
    wait_same = client._reserve_payload_wait("sig_abc")
    wait_other = client._reserve_payload_wait("sig_other")
    assert wait_same > 0.10
    assert wait_other == 0.0


def test_bug_016_self_heals_legacy_qps_exhausted_quota_file(tmp_path, monkeypatch):
    """BUG-016: quota_exhausted.json cũ (không có schema_version=2) chứa key bị khóa oan do 429 QPS
    phải tự phục hồi mở khóa lại khi get_rpd_status() hoặc GeminiClient() nạp lại."""
    import json
    from tomtat.llm import get_rpd_status

    cycle_date = "2026-11-19"
    monkeypatch.setattr("tomtat.llm._get_quota_cycle_date", lambda: cycle_date)
    keys = [f"key_test_{i:04d}" for i in range(1, 6)]
    monkeypatch.setattr("tomtat.config.get_api_keys", lambda root=None: keys)

    cfg = load_config()
    work_dir = tmp_path / "work"
    cfg["paths"] = {"work": str(work_dir)}
    logs_dir = work_dir / "S95587" / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Ghi log thật: key_1 ở 4-hydrogen và key_5 ở 3.8 hết RPD thật; key_1..4 ở 3.8 và 3.6 bị QPS
    def write_ev(fn, model, tail, err, action="mark_exhausted_rpd"):
        (logs_dir / fn).write_text(
            json.dumps({
                "model": model,
                "key_label": f"Key #1 (...{tail})",
                "action": action,
                "error": err,
                "in_progress": False,
                "phase": "done",
            }),
            encoding="utf-8",
        )

    write_ev("20261120-025400-000001_extract_1_t1.json", "gemini-4-hydrogen", "0001",
             "RateLimitError: Rate limit exceeded for model gemini-4-hydrogen (limit: 20 requests per day on Free Tier).")
    for idx, tail in enumerate(("0001", "0002", "0003", "0004"), start=1):
        write_ev(f"20261120-025500-00000{idx}_extract_1_t{idx}.json", "gemini-3.8-flash", tail,
                 "RateLimitError: The query-per-second (QPS) limit for identical or near-identical request payloads has been exceeded.")
        write_ev(f"20261120-025600-00000{idx}_extract_1_t{idx}.json", "gemini-3.6-flash", tail,
                 "RateLimitError: The query-per-second (QPS) limit for identical or near-identical request payloads has been exceeded.")
    write_ev("20261120-025700-000009_extract_1_t9.json", "gemini-3.8-flash", "0005",
             "RateLimitError: Rate limit exceeded for model gemini-3.8-flash (limit: 20 requests per day on Free Tier).")

    # Tạo quota_exhausted.json legacy chưa có schema_version
    quota_file = work_dir / "quota_exhausted.json"
    quota_file.write_text(
        json.dumps({
            "date": cycle_date,
            "exhausted": [
                {"key": "key_test_0001", "model": "gemini-4-hydrogen"},
                *[{"key": k, "model": "gemini-3.8-flash"} for k in keys],
                *[{"key": k, "model": "gemini-3.6-flash"} for k in keys[:4]],
            ],
            "rpd_counts": {"gemini-4-hydrogen": 20, "gemini-3.8-flash": 24, "gemini-3.6-flash": 5},
        }),
        encoding="utf-8",
    )

    rpd_map = {x["model"]: x for x in get_rpd_status(cfg, api_keys=keys)}
    assert rpd_map["gemini-4-hydrogen"]["exhausted_keys"] == 1
    assert rpd_map["gemini-3.8-flash"]["exhausted_keys"] == 1  # chỉ còn key_5 hết RPD thật
    assert rpd_map["gemini-3.6-flash"]["exhausted_keys"] == 0  # mở khóa toàn bộ 4 key bị QPS

    saved = json.loads(quota_file.read_text(encoding="utf-8"))
    assert saved["schema_version"] == 2

    # GeminiClient nạp vào cũng phải thấy đúng tập _exhausted đã phục hồi
    client = GeminiClient(cfg, keys)
    assert ("key_test_0001", "gemini-4-hydrogen") in client._exhausted
    assert ("key_test_0005", "gemini-3.8-flash") in client._exhausted
    assert ("key_test_0001", "gemini-3.8-flash") not in client._exhausted
    assert ("key_test_0001", "gemini-3.6-flash") not in client._exhausted


def test_bug_016_get_file_logs_respects_saved_net_try_one_after_previous_errors(tmp_path):
    """BUG-016: Khi lần chạy mới ghi net_try=1 sau các log lỗi cũ cùng (step, attempt),
    FileService.get_file_logs phải giữ nguyên net_try=1 (không hiển thị nhầm thành #59)."""
    import json
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
    ws = svc.get_workspace("S95587")
    ws.logs_dir.mkdir(parents=True, exist_ok=True)

    # 58 log lỗi cũ (ví dụ còn sót trong cửa sổ limit=60)
    for i in range(1, 59):
        (ws.logs_dir / f"20261120-0300{i:02d}-000000_extract_1_t{i}.json").write_text(
            json.dumps({
                "step": "extract",
                "attempt": 1,
                "net_try": i,
                "model": "gemini-3.1-flash-lite",
                "started_at": f"2026-11-20T03:00:{i:02d}",
                "in_progress": False,
                "phase": "done",
                "error": "RateLimitError: 429",
            }),
            encoding="utf-8",
        )
    # 2 log của lần chạy mới: net_try=1 và net_try=2
    (ws.logs_dir / "20261120-032734-000000_extract_1_t1.json").write_text(
        json.dumps({
            "step": "extract",
            "attempt": 1,
            "net_try": 1,
            "model": "gemini-4-hydrogen",
            "started_at": "2026-11-20T03:27:34",
            "in_progress": False,
            "phase": "done",
            "error": "InternalServerError: 503",
        }),
        encoding="utf-8",
    )
    (ws.logs_dir / "20261120-032738-000000_extract_1_t2.json").write_text(
        json.dumps({
            "step": "extract",
            "attempt": 1,
            "net_try": 2,
            "model": "gemini-4-hydrogen",
            "started_at": "2026-11-20T03:27:38",
            "in_progress": False,
            "phase": "done",
            "error": "RateLimitError: 429",
        }),
        encoding="utf-8",
    )

    logs = svc.get_file_logs("S95587", limit=60)["logs"]
    # Mới nhất lên đầu
    assert logs[0]["filename"].endswith("_t2.json")
    assert logs[0]["net_try"] == 2
    assert logs[1]["filename"].endswith("_t1.json")
    assert logs[1]["net_try"] == 1


# ---------------------------------------------------------------------------
# BUG-017: Lưu version và rollback về version trước của mỗi file
# ---------------------------------------------------------------------------

def _setup_version_test_env(tmp_path, monkeypatch):
    cfg = load_config()
    cfg["paths"] = {
        "input": str(tmp_path / "input"),
        "output": str(tmp_path / "output"),
        "work": str(tmp_path / "work"),
        "prompts": str(cfg.prompts_dir),
        "report": str(tmp_path / "output" / "baocao.xlsx"),
    }
    cfg["judge"]["enabled"] = False
    monkeypatch.setattr(cfg, "reload", lambda: None)
    inp = tmp_path / "input" / "M54629.docx"
    inp.parent.mkdir(parents=True, exist_ok=True)
    inp.write_bytes((cfg.root / "samples" / "M54629.docx").read_bytes())
    return cfg, inp


def test_bug_017_version_save_auto_save_and_rollback_flow(tmp_path, monkeypatch):
    """BUG-017: auto_save không spam vNNN.json; lưu version & rollback (cụ thể / version trước) hoạt động an toàn, không mất dữ liệu."""
    from tomtat.llm import MockClient
    from tomtat.pipeline import process_file
    from tomtat.state import FileWorkspace
    from tomtat.web import create_app

    cfg, inp = _setup_version_test_env(tmp_path, monkeypatch)
    client = MockClient(cfg)

    # 0. only_step='inspect' không được tạo bản nháp v001 rỗng
    process_file(inp, cfg, client, only_step="inspect")
    ws = FileWorkspace(cfg.work_dir, inp)
    assert ws.list_draft_versions() == []

    # 1. Chạy pipeline AI tạo v001
    out1 = process_file(inp, cfg, client, force=True)
    assert out1["state"]["current_version"] == 1
    v1_abs = out1["state"]["abstract"]["text"]
    v1_ext = out1["state"]["extract"]["text"]
    v1_style = out1["state"]["label"]["value"]

    app = create_app(cfg, mock=True)
    tc = app.test_client()

    # 2. auto_save=True chỉ cập nhật state.json, KHÔNG tạo v002.json; báo has_unsaved_changes=True
    edited_abs_1 = "Bản biên soạn chỉnh sửa tạm thời lần 1."
    r_auto = tc.post(
        "/api/file/M54629/save",
        json={
            "style": v1_style,
            "extract": v1_ext,
            "abstract": edited_abs_1,
            "auto_save": True,
        },
    )
    assert r_auto.status_code == 200
    d_auto = r_auto.get_json()
    assert d_auto["saved_new_version"] is False
    assert d_auto["version"] == 1
    assert d_auto["has_unsaved_changes"] is True
    assert d_auto["previous_version"] == 1
    assert len(ws.list_draft_versions()) == 1

    # 3. Giả lập có judge và lưu version thủ công kèm ghi chú -> tạo v002 (chỉnh nhẹ v1_abs để vẫn đạt mọi check cơ học khi duyệt)
    st_now = ws.load_state()
    st_now["judge"] = {"average": 4.6, "relevance": 4.6, "coherence": 4.6, "consistency": 4.6, "fluency": 4.6}
    ws.save_state(st_now)
    v1_words = v1_abs.split()
    assert len(v1_words) > 20
    edited_abs_2 = " ".join(v1_words[:-3]).rstrip(".,;:") + "."
    assert edited_abs_2 != v1_abs
    r_save = tc.post(
        "/api/file/M54629/save",
        json={
            "style": v1_style,
            "extract": v1_ext,
            "abstract": edited_abs_2,
            "note": "Chốt bản biên soạn v2",
            "auto_save": False,
        },
    )
    assert r_save.status_code == 200
    d_save = r_save.get_json()
    assert d_save["saved_new_version"] is True
    assert d_save["version"] == 2
    assert d_save["has_unsaved_changes"] is False
    assert d_save["previous_version"] == 1
    assert d_save["versions"][0]["source"] == "user"
    assert d_save["versions"][0]["note"] == "Chốt bản biên soạn v2"

    # GET /api/file/<stem>/versions trả về danh sách mới nhất lên đầu
    r_vers = tc.get("/api/file/M54629/versions").get_json()
    assert [v["version"] for v in r_vers["versions"]] == [2, 1]
    assert r_vers["previous_version"] == 1

    # 4. Duyệt xuất ở v002 (không sửa thêm gì) -> giữ nguyên v002 (không nhảy thành v003) và gắn cờ approved=True;
    #    sau đó rollback 'previous' lùi về v001 (xóa sạch judge, đưa status về da_co_nhap, xóa output_path)
    r_app = tc.post(
        "/api/file/M54629/approve",
        json={"style": v1_style, "extract": v1_ext, "abstract": edited_abs_2, "confirm_warnings": True},
    )
    assert r_app.status_code == 200
    d_app = r_app.get_json()
    assert d_app["ok"] is True
    assert d_app["state"]["current_version"] == 2
    assert len(d_app["versions"]) == 2
    assert d_app["versions"][0]["version"] == 2
    assert d_app["versions"][0]["source"] == "user"
    assert d_app["versions"][0]["approved"] is True

    r_rb1 = tc.post("/api/file/M54629/restore", json={"version": "previous"})
    assert r_rb1.status_code == 200
    d_rb1 = r_rb1.get_json()
    assert d_rb1["ok"] is True
    assert d_rb1["restored_from"] == 1
    assert d_rb1["version"] == 3
    assert d_rb1["state"]["abstract"]["text"] == v1_abs
    assert d_rb1["state"]["judge"] is None
    assert d_rb1["state"]["status"] == "da_co_nhap"
    assert d_rb1["state"]["output_path"] is None

    # 5. Bấm rollback 'previous' lần nữa (hoàn tác rollback) -> quay lại bản trước đó (v002)
    r_rb2 = tc.post("/api/file/M54629/restore", json={})
    assert r_rb2.status_code == 200
    d_rb2 = r_rb2.get_json()
    assert d_rb2["restored_from"] == 2
    assert d_rb2["state"]["abstract"]["text"] == edited_abs_2

    # 6. Đang có chỉnh sửa chưa lưu version (truyền kèm trong /restore) -> phải tự chụp auto:before_restore trước khi khôi phục v001
    unsaved_abs = "Nội dung đang gõ dở chưa kịp bấm lưu version."
    r_rb3 = tc.post(
        "/api/file/M54629/restore",
        json={
            "version": 1,
            "style": v1_style,
            "extract": v1_ext,
            "abstract": unsaved_abs,
        },
    )
    assert r_rb3.status_code == 200
    d_rb3 = r_rb3.get_json()
    assert d_rb3["restored_from"] == 1
    assert d_rb3["state"]["abstract"]["text"] == v1_abs
    # Kiểm tra bản auto:before_restore đã lưu lại đúng unsaved_abs
    before_snaps = [v for v in d_rb3["versions"] if v.get("source") == "auto:before_restore"]
    assert before_snaps and before_snaps[0]["abstract"]["text"] == unsaved_abs


def test_bug_017_pre_ai_backup_and_no_duplicate_resume_versions(tmp_path, monkeypatch):
    """BUG-017: Trước khi AI chạy lại (kể cả khi restart=True thất bại giữa chừng) phải tự lưu bản sửa tay;
    chạy resume khi không gọi AI thì không đẻ thêm version trùng lặp."""
    from tomtat.llm import LLMError, MockClient
    from tomtat.pipeline import process_file
    from tomtat.services import FileService
    from tomtat.state import FileWorkspace

    cfg, inp = _setup_version_test_env(tmp_path, monkeypatch)
    client = MockClient(cfg)
    svc = FileService(cfg)

    process_file(inp, cfg, client, force=True)
    ws = FileWorkspace(cfg.work_dir, inp)
    assert len(ws.list_draft_versions()) == 1

    # Chạy lại với restart=False khi mọi bước đã đạt chuẩn -> KHÔNG tạo thêm version trùng
    process_file(inp, cfg, client, force=True, restart=False)
    assert len(ws.list_draft_versions()) == 1

    # Sửa tay bằng auto-save (create_version=False)
    st = ws.load_state()
    manual_abs = "Bản biên soạn người dùng tự sửa trước khi bấm chạy lại AI."
    svc.save_draft(
        stem="M54629",
        style=st["label"]["value"],
        extract=st["extract"]["text"],
        abstract=manual_abs,
        create_version=False,
    )
    assert len(ws.list_draft_versions()) == 1

    # Chạy lại cả bài (restart=True) nhưng LLM lỗi giữa chừng -> bản sửa tay vẫn còn trong auto:before_ai và khôi phục được
    class FailingClient(MockClient):
        def generate_json(self, *args, **kwargs):
            raise LLMError("Simulated 503 outage")

    res_fail = process_file(inp, cfg, FailingClient(cfg), force=True, restart=True)
    assert "error" in res_fail
    versions = ws.list_draft_versions()
    assert len(versions) == 2
    assert versions[0]["source"] == "auto:before_ai"
    assert versions[0]["abstract"]["text"] == manual_abs

    rb = svc.restore_draft("M54629", version=None)
    assert rb["state"]["abstract"]["text"] == manual_abs


def test_bug_017_numeric_version_sort_legacy_snapshot_and_concurrency_guard(tmp_path, monkeypatch):
    """BUG-017: Sắp xếp số phiên bản chuẩn (> v999), tương thích snapshot cũ thiếu field, và chặn 409 khi file đang chạy AI."""
    import json
    from tomtat.llm import MockClient
    from tomtat.pipeline import process_file
    from tomtat.state import FileWorkspace
    from tomtat.web import create_app

    cfg, inp = _setup_version_test_env(tmp_path, monkeypatch)
    client = MockClient(cfg)
    process_file(inp, cfg, client, force=True)
    ws = FileWorkspace(cfg.work_dir, inp)
    st = ws.load_state()

    # 1. Tạo snapshot kiểu cũ (thiếu status, flags, model_used, compress) ở v002 và v1000
    legacy_snap = {
        "version": 2,
        "saved_at": "2026-11-19T10:00:00.000",
        "source": "user",
        "note": "Legacy v2",
        "label": {"value": st["label"]["value"]},
        "extract": {
            "selections": st["extract"]["selections"],
            "text": st["extract"]["text"],
            "words": st["extract"]["words"],
            "custom_edited": False,
        },
        "abstract": {
            "text": st["abstract"]["text"],
            "words": st["abstract"]["words"],
        },
        "judge": None,
        "checks_summary": {"ok": True, "n_errors": 0, "n_warnings": 0},
    }
    (ws.drafts_dir / "v002.json").write_text(json.dumps(legacy_snap, ensure_ascii=False), encoding="utf-8")
    snap_1000 = dict(legacy_snap, version=1000, note="Version 1000")
    (ws.drafts_dir / "v1000.json").write_text(json.dumps(snap_1000, ensure_ascii=False), encoding="utf-8")

    # Sắp xếp số học: 1000 phải đứng trước 2 và 1 (không bị v002 > v1000 theo chữ cái)
    vers = ws.list_draft_versions()
    assert [v["version"] for v in vers] == [1000, 2, 1]

    app = create_app(cfg, mock=True)
    tc = app.test_client()
    mgr = app.config["TOMTAT_BATCH_MGR"]

    # 2. Khi file đang chạy AI -> /save và /restore phải trả về 409
    ok, _ = mgr.try_acquire_file("M54629", "abstract")
    assert ok is True
    try:
        r_s = tc.post("/api/file/M54629/save", json={"style": st["label"]["value"], "extract": st["extract"]["text"], "abstract": "x"})
        r_r = tc.post("/api/file/M54629/restore", json={"version": 2})
        assert r_s.status_code == 409 and r_s.get_json()["already_running"] is True
        assert r_r.status_code == 409 and r_r.get_json()["already_running"] is True
    finally:
        mgr.release_file("M54629")

    # 3. Khôi phục snapshot legacy v002 thành công -> tạo v1001 và tính lại compress
    r_ok = tc.post("/api/file/M54629/restore", json={"version": 2})
    assert r_ok.status_code == 200
    d_ok = r_ok.get_json()
    assert d_ok["version"] == 1001
    assert d_ok["state"]["extract"]["compress"] > 0


def test_bug_017_cli_versions_and_frontend_contract(tmp_path, monkeypatch, capsys):
    """BUG-017: CLI `versions` (--save, --rollback) và HTML/JS giao diện có đủ nút lưu/rollback version."""
    import yaml
    from tomtat.cli import main as cli_main
    from tomtat.llm import MockClient
    from tomtat.pipeline import process_file

    cfg, inp = _setup_version_test_env(tmp_path, monkeypatch)
    cfg_file = tmp_path / "test_config.yaml"
    cfg_file.write_text(yaml.safe_dump(dict(cfg), allow_unicode=True), encoding="utf-8")

    process_file(inp, cfg, MockClient(cfg), force=True)

    assert cli_main(["--config", str(cfg_file), "versions", "M54629", "--save", "--note", "Mốc CLI"]) == 0
    assert "v002" in capsys.readouterr().out

    assert cli_main(["--config", str(cfg_file), "versions", "M54629"]) == 0
    out_list = capsys.readouterr().out
    assert "v002" in out_list and "v001" in out_list

    assert cli_main(["--config", str(cfg_file), "versions", "M54629.docx", "--rollback", "1"]) == 0
    assert "v001" in capsys.readouterr().out

    html = (PROJECT_ROOT / "src" / "tomtat" / "web" / "templates" / "index.html").read_text(encoding="utf-8")
    for elem_id in (
        'id="version-bar"',
        'id="cur-version-badge"',
        'id="version-dirty-badge"',
        'id="version-note"',
        'id="btn-save"',
        'id="btn-rollback-prev"',
        'id="btn-versions-modal"',
        'id="versions-card"',
        'id="versions-list"',
    ):
        assert elem_id in html, f"Thiếu phần tử UI {elem_id} trong index.html"

    editor_js = (PROJECT_ROOT / "src" / "tomtat" / "web" / "static" / "modules" / "editor.js").read_text(encoding="utf-8")
    assert "auto_save: true" in editor_js and "create_version: false" in editor_js
    assert "create_version: true" in editor_js
    assert "rollbackPreviousVersion" in editor_js


# ---------------------------------------------------------------------------
# BUG-018: Duyệt ở v002 (không sửa thêm) bị nhảy oan thành v003 trùng lặp
# ---------------------------------------------------------------------------

def test_bug_018_approve_keeps_current_version_when_unchanged_and_creates_new_when_edited(tmp_path, monkeypatch):
    """BUG-018: Khi bấm Duyệt trên nội dung khớp với v002 hiện tại, phải giữ nguyên v002 (gắn cờ approved=True)
    thay vì tạo thêm v003 trùng hệt; chỉ tạo version mới khi có thay đổi nội dung trước khi duyệt."""
    from tomtat.llm import MockClient
    from tomtat.pipeline import process_file
    from tomtat.state import FileWorkspace
    from tomtat.web import create_app

    cfg, inp = _setup_version_test_env(tmp_path, monkeypatch)
    client = MockClient(cfg)

    # Tạo v001 và chạy lại để có v002
    process_file(inp, cfg, client, force=True)
    process_file(inp, cfg, client, force=True, restart=True)
    ws = FileWorkspace(cfg.work_dir, inp)
    st2 = ws.load_state()
    assert st2["current_version"] == 2
    assert len(ws.list_draft_versions()) == 2

    app = create_app(cfg, mock=True)
    tc = app.test_client()

    # 1. Bấm Duyệt trực tiếp ở v002 (không đổi nội dung) -> giữ nguyên v002, KHÔNG đẻ ra v003
    r_app1 = tc.post(
        "/api/file/M54629/approve",
        json={
            "style": st2["label"]["value"],
            "extract": st2["extract"]["text"],
            "abstract": st2["abstract"]["text"],
            "confirm_warnings": True,
        },
    )
    assert r_app1.status_code == 200
    d1 = r_app1.get_json()
    assert d1["ok"] is True
    assert d1["state"]["current_version"] == 2
    assert len(d1["versions"]) == 2
    v2 = d1["versions"][0]
    assert v2["version"] == 2
    assert v2["source"] == "ai:pipeline"
    assert v2["approved"] is True
    assert v2["approved_at"]
    assert v2["status"] == "da_xuat"

    # 2. Nếu sửa nội dung trước khi bấm Duyệt (chưa bấm Lưu version) -> phải tạo v003 mới với approved=True
    words = st2["abstract"]["text"].split()
    edited_abs = " ".join(words[:-3]).rstrip(".,;:") + "."
    r_app2 = tc.post(
        "/api/file/M54629/approve",
        json={
            "style": st2["label"]["value"],
            "extract": st2["extract"]["text"],
            "abstract": edited_abs,
            "confirm_warnings": True,
        },
    )
    assert r_app2.status_code == 200
    d2 = r_app2.get_json()
    assert d2["ok"] is True
    assert d2["state"]["current_version"] == 3
    assert len(d2["versions"]) == 3
    v3 = d2["versions"][0]
    assert v3["version"] == 3
    assert v3["source"] == "approve"
    assert v3["approved"] is True

    # 3. Giao diện editor.js & modals.js phải hiển thị huy hiệu Đã duyệt cho version có v.approved
    editor_js = (PROJECT_ROOT / "src" / "tomtat" / "web" / "static" / "modules" / "editor.js").read_text(encoding="utf-8")
    modals_js = (PROJECT_ROOT / "src" / "tomtat" / "web" / "static" / "modules" / "modals.js").read_text(encoding="utf-8")
    assert "v.approved" in editor_js and "v.approved" in modals_js


# ---------------------------------------------------------------------------
# BUG-019: AI: Gán nhãn lại chỉ báo cần chỉnh sửa phần khác khi nhãn thay đổi
# ---------------------------------------------------------------------------

def test_bug_019_relabel_marks_stale_only_when_label_differs(tmp_path, monkeypatch):
    """BUG-019: Khi chạy AI: Gán nhãn lại:
    - Nếu nhãn mới giống nhãn trước đó -> không đánh dấu stale_steps / stale_reason.
    - Nếu nhãn mới khác nhãn trước đó -> đánh dấu stale các phần sau đã có nội dung (không báo oan judge khi judge=None).
    - Chạy lại từng phần hoặc sửa tay + lưu version / duyệt xuất sẽ gỡ stale tương ứng."""
    from tomtat.llm import MockClient
    from tomtat.pipeline import process_file
    from tomtat.state import FileWorkspace
    from tomtat.web import create_app

    cfg, inp = _setup_version_test_env(tmp_path, monkeypatch)

    class SwitchableLabelClient(MockClient):
        def __init__(self, c, forced_label: str | None = None):
            super().__init__(c)
            self.forced_label = forced_label

        def _mock_label(self, ctx, attempt):
            res = super()._mock_label(ctx, attempt)
            if self.forced_label:
                res["label"] = self.forced_label
                for p in res.get("per_doc") or []:
                    p["label"] = self.forced_label
            return res

    # 0. File mới tinh chưa có nhãn/extract/abstract: chạy only_step='label' không được báo stale
    client_init = SwitchableLabelClient(cfg)
    out_fresh = process_file(inp, cfg, client_init, only_step="label")
    assert out_fresh["label_changed"] is False
    assert out_fresh["state"]["stale_steps"] == []
    assert out_fresh["state"]["stale_reason"] is None

    # Chạy đủ pipeline để file có nháp hoàn chỉnh (extract + abstract, judge=None)
    out_full = process_file(inp, cfg, client_init, force=True, restart=True)
    orig_label = out_full["state"]["label"]["value"]
    alt_label = "chính luận" if orig_label != "chính luận" else "khoa học"
    ws = FileWorkspace(cfg.work_dir, inp)
    assert ws.load_state()["stale_steps"] == []

    app = create_app(cfg, mock=True)
    tc = app.test_client()

    # 1. Chạy AI: Gán nhãn lại mà ra ĐÚNG nhãn trước đó -> KHÔNG báo stale_steps, label_changed=False
    same_client = SwitchableLabelClient(cfg, forced_label=orig_label)
    monkeypatch.setattr("tomtat.web.routes.process.make_client", lambda c, force_mock=False: same_client)
    r_same = tc.post("/api/file/M54629/rerun", json={"step": "label", "mock": True})
    assert r_same.status_code == 200
    d_same = r_same.get_json()
    assert d_same["ok"] is True
    assert d_same["label_changed"] is False
    assert d_same["previous_label"] == orig_label
    assert d_same["new_label"] == orig_label
    assert d_same["state"]["stale_steps"] == []
    assert d_same["state"]["stale_reason"] is None

    # 2. Chạy AI: Gán nhãn lại ra nhãn KHÁC trước đó -> báo cần chỉnh sửa extract & abstract (KHÔNG báo judge vì judge=None)
    diff_client = SwitchableLabelClient(cfg, forced_label=alt_label)
    monkeypatch.setattr("tomtat.web.routes.process.make_client", lambda c, force_mock=False: diff_client)
    r_diff = tc.post("/api/file/M54629/rerun", json={"step": "label", "mock": True})
    assert r_diff.status_code == 200
    d_diff = r_diff.get_json()
    assert d_diff["ok"] is True
    assert d_diff["label_changed"] is True
    assert d_diff["previous_label"] == orig_label
    assert d_diff["new_label"] == alt_label
    assert d_diff["state"]["label"]["value"] == alt_label
    assert d_diff["state"]["stale_steps"] == ["extract", "abstract"]
    assert orig_label in d_diff["state"]["stale_reason"]
    assert alt_label in d_diff["state"]["stale_reason"]

    # 3. Nếu đang có stale ['abstract'] (do chạy extract) mà chạy lại AI: Gán nhãn lại cùng nhãn -> KHÔNG làm mất stale ['abstract']
    r_ext = tc.post("/api/file/M54629/rerun", json={"step": "extract", "mock": True})
    assert r_ext.status_code == 200
    assert r_ext.get_json()["state"]["stale_steps"] == ["abstract"]
    r_same_after_ext = tc.post("/api/file/M54629/rerun", json={"step": "label", "mock": True})
    assert r_same_after_ext.status_code == 200
    assert r_same_after_ext.get_json()["label_changed"] is False
    assert r_same_after_ext.get_json()["state"]["stale_steps"] == ["abstract"]

    # 4. Nếu sửa tay abstract và Lưu version (create_version=True) -> gỡ 'abstract' khỏi stale_steps
    st_now = ws.load_state()
    abs_words = st_now["abstract"]["text"].split()
    edited_abs = " ".join(abs_words[:-2]).rstrip(".,;:") + "."
    r_save = tc.post(
        "/api/file/M54629/save",
        json={
            "style": alt_label,
            "extract": st_now["extract"]["text"],
            "abstract": edited_abs,
            "auto_save": False,
        },
    )
    assert r_save.status_code == 200
    assert r_save.get_json()["state"]["stale_steps"] == []
    assert r_save.get_json()["state"]["stale_reason"] is None

    # 5. Nếu có judge sẵn và gán nhãn lại đổi nhãn sang orig_label -> stale_steps phải gồm cả judge
    st_j = ws.load_state()
    st_j["judge"] = {"average": 4.5, "relevance": 4.5, "coherence": 4.5, "consistency": 4.5, "fluency": 4.5}
    ws.save_state(st_j)
    back_client = SwitchableLabelClient(cfg, forced_label=orig_label)
    monkeypatch.setattr("tomtat.web.routes.process.make_client", lambda c, force_mock=False: back_client)
    r_back = tc.post("/api/file/M54629/rerun", json={"step": "label", "mock": True})
    assert r_back.status_code == 200
    assert r_back.get_json()["label_changed"] is True
    assert r_back.get_json()["state"]["stale_steps"] == ["extract", "abstract", "judge"]

    # Duyệt & xuất phải xóa sạch stale_steps và stale_reason
    st_fin = ws.load_state()
    r_app = tc.post(
        "/api/file/M54629/approve",
        json={
            "style": orig_label,
            "extract": st_fin["extract"]["text"],
            "abstract": st_fin["abstract"]["text"],
            "confirm_warnings": True,
        },
    )
    assert r_app.status_code == 200
    assert r_app.get_json()["state"]["stale_steps"] == []
    assert r_app.get_json()["state"]["stale_reason"] is None


def test_bug_019_frontend_relabel_stale_contract():
    """BUG-019: Giao diện HTML/JS/CSS có đầy đủ cảnh báo đổi nhãn, câu hỏi và nút chạy lại Bản trích chọn & Bản biên soạn."""
    html = (PROJECT_ROOT / "src" / "tomtat" / "web" / "templates" / "index.html").read_text(encoding="utf-8")
    for elem_id in (
        'id="label-stale-prompt"',
        'id="card-extract"',
        'id="card-abstract"',
        'id="stale-badge-extract"',
        'id="stale-badge-abstract"',
        'id="stale-badge-judge"',
    ):
        assert elem_id in html, f"Thiếu {elem_id} trong index.html"

    editor_js = (PROJECT_ROOT / "src" / "tomtat" / "web" / "static" / "modules" / "editor.js").read_text(encoding="utf-8")
    app_js = (PROJECT_ROOT / "src" / "tomtat" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    sidebar_js = (PROJECT_ROOT / "src" / "tomtat" / "web" / "static" / "modules" / "sidebar.js").read_text(encoding="utf-8")
    assert "label_changed" in editor_js
    assert "STALE_PART_NAMES_VI" in editor_js
    assert "card-stale" in editor_js
    assert "stale_reason" in editor_js
    assert "extract_abstract" in editor_js and "extract_abstract" in sidebar_js
    assert "data-stale-rerun" in editor_js and "data-stale-dismiss" in editor_js
    assert "onStyleChange" in editor_js and "onStyleChange" in app_js


def test_bug_019_style_change_prompts_and_extract_abstract_action(tmp_path, monkeypatch):
    """BUG-019: Đổi phong cách thủ công (auto-save) tự đánh dấu stale; nút 'extract_abstract' chạy lại cả Trích chọn + Biên soạn và gỡ stale."""
    from tomtat.llm import MockClient
    from tomtat.pipeline import process_file
    from tomtat.state import FileWorkspace
    from tomtat.web import create_app

    cfg, inp = _setup_version_test_env(tmp_path, monkeypatch)
    init_client = MockClient(cfg)
    out_full = process_file(inp, cfg, init_client, force=True, restart=True)
    orig_style = out_full["state"]["label"]["value"]
    alt_style = "chính luận" if orig_style != "chính luận" else "khoa học"

    ws = FileWorkspace(cfg.work_dir, inp)
    app = create_app(cfg, mock=True)
    tc = app.test_client()

    # 1. Đổi phong cách thủ công qua /save (auto_save=True) -> tự đánh dấu extract & abstract cần sửa
    st0 = ws.load_state()
    r_chg = tc.post(
        "/api/file/M54629/save",
        json={
            "style": alt_style,
            "extract": st0["extract"]["text"],
            "abstract": st0["abstract"]["text"],
            "auto_save": True,
        },
    )
    assert r_chg.status_code == 200
    d_chg = r_chg.get_json()
    assert d_chg["saved_new_version"] is False
    assert d_chg["state"]["stale_steps"] == ["extract", "abstract"]
    assert orig_style in d_chg["state"]["stale_reason"]
    assert alt_style in d_chg["state"]["stale_reason"]

    # 2. Đổi ngược về phong cách gốc của snapshot sạch (v001) -> tự bỏ stale_steps
    r_rev = tc.post(
        "/api/file/M54629/save",
        json={
            "style": orig_style,
            "extract": st0["extract"]["text"],
            "abstract": st0["abstract"]["text"],
            "auto_save": True,
        },
    )
    assert r_rev.status_code == 200
    assert r_rev.get_json()["state"]["stale_steps"] == []
    assert r_rev.get_json()["state"]["stale_reason"] is None

    # 3. Đổi sang phong cách mới rồi bấm nút chạy lại cả Bản trích chọn & Bản biên soạn (step='extract_abstract')
    tc.post(
        "/api/file/M54629/save",
        json={
            "style": alt_style,
            "extract": st0["extract"]["text"],
            "abstract": st0["abstract"]["text"],
            "auto_save": True,
        },
    )
    spy_client = MockClient(cfg)
    monkeypatch.setattr("tomtat.web.routes.process.make_client", lambda c, force_mock=False: spy_client)
    r_both = tc.post(
        "/api/file/M54629/rerun",
        json={
            "step": "extract_abstract",
            "style": alt_style,
            "mock": True,
            "ignore_mismatch": True,
        },
    )
    assert r_both.status_code == 200
    d_both = r_both.get_json()
    assert d_both["ok"] is True
    assert "label" not in spy_client.calls
    assert "extract" in spy_client.calls and "abstract" in spy_client.calls
    assert d_both["state"]["label"]["value"] == alt_style
    assert d_both["state"]["stale_steps"] == []
    assert d_both["state"]["stale_reason"] is None
    assert d_both["versions"][0]["source"] == "ai:extract_abstract"

    # 4. Kiểm tra nút "Giữ nguyên (không sửa)" (dismiss_stale=True) xóa được stale_steps
    r_chg2 = tc.post(
        "/api/file/M54629/save",
        json={
            "style": orig_style,
            "extract": d_both["state"]["extract"]["text"],
            "abstract": d_both["state"]["abstract"]["text"],
            "auto_save": True,
        },
    )
    assert r_chg2.get_json()["state"]["stale_steps"] == ["extract", "abstract"]
    r_dis = tc.post(
        "/api/file/M54629/save",
        json={
            "style": orig_style,
            "extract": d_both["state"]["extract"]["text"],
            "abstract": d_both["state"]["abstract"]["text"],
            "auto_save": True,
            "dismiss_stale": True,
        },
    )
    assert r_dis.status_code == 200
    assert r_dis.get_json()["state"]["stale_steps"] == []
    assert r_dis.get_json()["state"]["stale_reason"] is None


# ---------------------------------------------------------------------------
# BUG-020: Loại bỏ hoàn toàn phần "Ghi chú gửi cho AI khi chạy lại" (#ai-note / user_note)
# ---------------------------------------------------------------------------

def test_bug_020_ai_rerun_note_removed_cleanly(tmp_path, monkeypatch):
    """BUG-020: Bỏ hoàn toàn ô nhập #ai-note và luồng user_note ở frontend, prompt và backend,
    nhưng vẫn giữ nguyên #version-note và các nút AI chạy lại hoạt động bình thường."""
    import inspect
    from tomtat.pipeline import process_file, step_abstract, step_extract, step_label
    from tomtat.services.file_service import FileService
    from tomtat.web import create_app

    # 1. Hợp đồng frontend (HTML / CSS / JS)
    html = (PROJECT_ROOT / "src" / "tomtat" / "web" / "templates" / "index.html").read_text(encoding="utf-8")
    assert "ai-note" not in html
    assert "Ghi chú gửi cho AI" not in html
    assert 'id="version-note"' in html
    for step in ("label", "extract", "abstract", "judge", "all"):
        assert f'data-rerun="{step}"' in html

    css = (PROJECT_ROOT / "src" / "tomtat" / "web" / "static" / "app.css").read_text(encoding="utf-8")
    assert ".ai-rerun-bar input" not in css

    static_dir = PROJECT_ROOT / "src" / "tomtat" / "web" / "static"
    for js_file in static_dir.rglob("*.js"):
        assert "ai-note" not in js_file.read_text(encoding="utf-8"), (
            f"{js_file.name} vẫn còn tham chiếu tới #ai-note"
        )

    # 2. Hợp đồng prompt và chữ ký backend
    for pname in ("label.md", "extract.md", "abstract.md"):
        ptxt = (PROJECT_ROOT / "prompts" / pname).read_text(encoding="utf-8")
        assert "user_note" not in ptxt, f"prompts/{pname} vẫn còn user_note"

    for fn in (process_file, step_label, step_extract, step_abstract, FileService.process, FileService.process_async):
        assert "user_note" not in inspect.signature(fn).parameters

    # 3. API /rerun chạy thành công (cả khi không gửi note và khi client cũ gửi dư field note)
    cfg, inp = _setup_version_test_env(tmp_path, monkeypatch)
    app = create_app(cfg, mock=True)
    tc = app.test_client()

    r1 = tc.post("/api/file/M54629/rerun", json={"step": "abstract", "mock": True, "ignore_mismatch": True})
    assert r1.status_code == 200
    d1 = r1.get_json()
    assert d1["ok"] is True
    assert d1["versions"][0]["source"] == "ai:abstract"
    assert d1["versions"][0]["note"] == ""

    r2 = tc.post(
        "/api/file/M54629/rerun",
        json={"step": "abstract", "note": "ghi chu cu", "mock": True, "ignore_mismatch": True},
    )
    assert r2.status_code == 200
    d2 = r2.get_json()
    assert d2["ok"] is True
    assert d2["versions"][0]["note"] == ""


# ---------------------------------------------------------------------------
# BUG-021: Thiếu nút Dừng và cơ chế hủy luồng AI khi bấm 1 trong 5 nút AI chạy lại
# ---------------------------------------------------------------------------

def test_bug_021_stop_single_file_rerun_via_file_stop_and_batch_stop(tmp_path, monkeypatch):
    """BUG-021: Khi bấm chạy lại AI đơn lẻ (/rerun), người dùng phải dừng được ngay qua
    /api/file/<stem>/stop hoặc /api/batch/stop mà không bị biến thành trạng thái lỗi."""
    import json
    import threading
    import time as _time
    from tomtat.llm import MockClient
    from tomtat.pipeline import process_file
    from tomtat.state import FileWorkspace
    from tomtat.web import create_app

    cfg, inp = _setup_version_test_env(tmp_path, monkeypatch)
    # 1. Tạo sẵn bản nháp hợp lệ v001 (da_co_nhap)
    process_file(inp, cfg, MockClient(cfg), force=True)
    ws = FileWorkspace(cfg.work_dir, inp)
    assert ws.load_state()["status"] == "da_co_nhap"

    entered = threading.Event()
    unblock = threading.Event()

    class BlockingMockClient(MockClient):
        def generate_json(self, step, system, prompt, schema, *, log_dir=None, attempt=0, context=None):
            from tomtat.llm import LLMCancelledError, get_active_cancel_event, write_log

            ev = get_active_cancel_event()
            rec = {
                "step": step,
                "attempt": attempt,
                "net_try": 1,
                "model": "mock",
                "key_label": "Mock",
                "started_at": "2026-11-20T06:30:00",
                "started_epoch": _time.time(),
                "in_progress": True,
                "phase": "calling_api",
                "status": "running",
            }
            lp = write_log(log_dir, rec)
            entered.set()
            while not unblock.is_set():
                if ev is not None and ev.wait(timeout=0.05):
                    rec.update(in_progress=False, phase="done", status="cancelled", error="Đã dừng theo yêu cầu của người dùng.")
                    write_log(log_dir, rec, log_path=lp)
                    raise LLMCancelledError("Đã dừng chạy AI theo yêu cầu của người dùng.")
            return super().generate_json(step, system, prompt, schema, log_dir=log_dir, attempt=attempt, context=context)

    monkeypatch.setattr("tomtat.web.routes.process.make_client", lambda _c, force_mock=False: BlockingMockClient(cfg))
    app = create_app(cfg, mock=True)
    tc = app.test_client()

    # Case A: Bấm "AI: Gán nhãn lại" rồi dừng bằng /api/file/M54629/stop
    res_box: dict = {}

    def _run_rerun(payload):
        t0 = _time.time()
        resp = tc.post("/api/file/M54629/rerun", json=payload)
        res_box["elapsed"] = _time.time() - t0
        res_box["status_code"] = resp.status_code
        res_box["body"] = resp.get_json()

    th = threading.Thread(target=_run_rerun, args=({"step": "label", "mock": True},), daemon=True)
    th.start()
    assert entered.wait(timeout=2.0), "Rerun không bắt đầu kịp thời"

    # Khi đang chạy, /api/files, /api/batch/status và /api/file/<stem>/logs đều phải thấy M54629 đang chạy
    files_info = tc.get("/api/files").get_json()
    assert "M54629" in files_info["running_stems"]
    bst_info = tc.get("/api/batch/status").get_json()
    assert "M54629" in bst_info["rerun_running"]
    logs_live = tc.get("/api/file/M54629/logs").get_json()
    assert "M54629" in logs_live["running_stems"]

    r_stop = tc.post("/api/file/M54629/stop")
    assert r_stop.status_code == 200
    assert r_stop.get_json()["stopped"] is True

    th.join(timeout=2.0)
    assert not th.is_alive(), "Luồng rerun phải dừng ngay lập tức khi bấm Dừng"
    assert res_box["status_code"] == 200
    assert res_box["elapsed"] < 1.5
    body = res_box["body"]
    assert body["cancelled"] is True
    assert body["ok"] is False
    # Không được rơi vào trạng thái 'loi', giữ nguyên trạng thái nháp hợp lệ trước đó
    assert body["state"]["status"] == "da_co_nhap"
    assert body["state"]["current_step"] is None
    assert body["state"]["error_message"] is None

    # Log phải được đóng thành status='cancelled', không bị kẹt in_progress=True
    latest_log = json.loads(sorted(ws.logs_dir.glob("*.json"))[-1].read_text(encoding="utf-8"))
    assert latest_log["in_progress"] is False
    assert latest_log["status"] == "cancelled"

    # Case B: Bấm "AI: Chạy lại cả bài" (restart=True) rồi dừng bằng nút Dừng chung (/api/batch/stop)
    entered.clear()
    res_box.clear()
    th2 = threading.Thread(target=_run_rerun, args=({"step": "all", "restart": True, "mock": True},), daemon=True)
    th2.start()
    assert entered.wait(timeout=2.0)

    r_bstop = tc.post("/api/batch/stop")
    assert r_bstop.status_code == 200
    assert "M54629" in r_bstop.get_json()["stopped_stems"]

    th2.join(timeout=2.0)
    assert not th2.is_alive()
    assert res_box["status_code"] == 200
    body2 = res_box["body"]
    assert body2["cancelled"] is True
    assert body2["state"]["status"] == "chua_xu_ly"
    assert body2["state"]["current_step"] is None
    assert body2["state"]["error_message"] is None
    # Version v001 trước đó vẫn còn nguyên để rollback nếu cần
    assert any(v["version"] == 1 for v in body2["versions"])

    # Khóa stem phải được giải phóng hoàn toàn -> có thể chạy lại ngay sau khi dừng
    monkeypatch.setattr("tomtat.web.routes.process.make_client", lambda _c, force_mock=False: MockClient(cfg))
    r_next = tc.post("/api/file/M54629/rerun", json={"step": "label", "mock": True})
    assert r_next.status_code == 200
    assert r_next.get_json()["ok"] is True


def test_bug_021_gemini_client_cancels_during_rpm_wait_and_inflight_call(tmp_path, monkeypatch):
    """BUG-021: GeminiClient phải hủy tức thì khi đang chờ giãn cách RPM (không trừ oan RPD & trả slot)
    và khi đang chờ phản hồi _call_once từ mạng (không rò rỉ semaphore và đóng log cancelled)."""
    import json
    import threading
    import time as _time
    import pytest
    from tomtat.llm import LLMCancelledError, bind_cancel_event, get_rpd_status

    monkeypatch.setattr("tomtat.llm._get_quota_cycle_date", lambda: "2026-01-01")
    keys = ["key_cancel_0001"]
    monkeypatch.setattr("tomtat.config.get_api_keys", lambda root=None: keys)

    cfg = load_config()
    cfg["paths"] = {"work": str(tmp_path / "work")}
    cfg["gemini"]["min_interval_s"] = {"gemini-4-hydrogen": 30.0}
    cfg["gemini"]["rpm_limits"] = {"gemini-4-hydrogen": 5}

    client = GeminiClient(cfg, keys)
    monkeypatch.setattr(client, "_get_or_create_client", lambda k: object())
    log_dir = tmp_path / "work" / "S95434" / "logs"

    # 1. Hủy trong lúc chờ RPM (eff_wait_s ~ 30s)
    monkeypatch.setattr(client, "_call_once", lambda *a, **kw: ('{"ok": true}', {}, "completed"))
    res1 = client.generate_json(step="label", system="sys", prompt="p1", schema={}, log_dir=log_dir, attempt=1)
    assert res1.data == {"ok": True}

    cancel_wait = threading.Event()
    threading.Timer(0.08, cancel_wait.set).start()
    t0 = _time.time()
    with pytest.raises(LLMCancelledError):
        with bind_cancel_event(cancel_wait):
            client.generate_json(step="label", system="sys", prompt="p2", schema={}, log_dir=log_dir, attempt=1)
    assert (_time.time() - t0) < 1.0

    # Slot RPM phải được trả lại, và lượt bị hủy lúc chờ RPM tuyệt đối không tính RPD
    assert len(client._model_rpm_tracker[("key_cancel_0001", "gemini-4-hydrogen")]) == 1
    rpd_after_wait_cancel = {x["model"]: x for x in get_rpd_status(cfg, api_keys=keys)}
    assert rpd_after_wait_cancel["gemini-4-hydrogen"]["used"] == 1
    logs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(log_dir.glob("*.json"))]
    assert logs[-1]["in_progress"] is False
    assert logs[-1]["status"] == "cancelled"
    assert logs[-1]["action"] == "cancelled_while_waiting"

    # 2. Hủy trong lúc đang gọi _call_once (chặn mạng dài)
    entered_api = threading.Event()
    release_api = threading.Event()

    def slow_call_once(*args, **kwargs):
        entered_api.set()
        release_api.wait(timeout=5.0)
        return '{"ok": true}', {}, "completed"

    monkeypatch.setattr(client, "_call_once", slow_call_once)
    client._model_rpm_tracker.clear()
    client._rpm_tracker.clear()
    client._next_allowed_ts.clear()

    cancel_inflight = threading.Event()
    t_trig = threading.Thread(
        target=lambda: (entered_api.wait(timeout=2.0), cancel_inflight.set()),
        daemon=True,
    )
    t_trig.start()
    t1 = _time.time()
    with pytest.raises(LLMCancelledError):
        with bind_cancel_event(cancel_inflight):
            client.generate_json(step="label", system="sys", prompt="p3", schema={}, log_dir=log_dir, attempt=1)
    assert (_time.time() - t1) < 1.0
    release_api.set()
    t_trig.join(timeout=1.0)

    # Semaphore phải được giải phóng hoàn toàn sau khi hủy
    assert client._semaphore.acquire(timeout=0.1) is True
    client._semaphore.release()

    logs2 = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(log_dir.glob("*.json"))]
    assert logs2[-1]["in_progress"] is False
    assert logs2[-1]["status"] == "cancelled"
    assert logs2[-1]["action"] == "cancelled_in_flight"


def test_bug_021_batch_stop_cancels_inflight_file_cleanly(tmp_path, monkeypatch):
    """BUG-021: Khi đang chạy batch và bấm /api/batch/stop, file đang chạy dở phải dừng ngay,
    không bị tính vào done_count hay errors."""
    import threading
    import time as _time
    from tomtat.llm import LLMCancelledError, MockClient, get_active_cancel_event
    from tomtat.web import create_app

    cfg, inp = _setup_version_test_env(tmp_path, monkeypatch)
    entered = threading.Event()
    unblock = threading.Event()

    class BlockingBatchClient(MockClient):
        def generate_json(self, step, system, prompt, schema, *, log_dir=None, attempt=0, context=None):
            ev = get_active_cancel_event()
            entered.set()
            while not unblock.is_set():
                if ev is not None and ev.wait(timeout=0.05):
                    raise LLMCancelledError("Đã dừng.")
            return super().generate_json(step, system, prompt, schema, log_dir=log_dir, attempt=attempt, context=context)

    app = create_app(cfg, mock=True)
    mgr = app.config["TOMTAT_BATCH_MGR"]
    mgr._client_factory = lambda _c, force_mock=False: BlockingBatchClient(cfg)
    tc = app.test_client()

    r_start = tc.post("/api/batch/start", json={"stems": ["M54629"], "concurrency": 1, "mock": True})
    assert r_start.get_json()["ok"] is True
    assert entered.wait(timeout=2.0)

    r_stop = tc.post("/api/batch/stop")
    assert r_stop.status_code == 200
    assert "M54629" in r_stop.get_json()["stopped_stems"]

    deadline = _time.time() + 2.0
    st = {}
    while _time.time() < deadline:
        st = tc.get("/api/batch/status").get_json()
        if not st["active"]:
            break
        _time.sleep(0.03)
    unblock.set()

    assert st["active"] is False
    assert st["stopped"] is True
    assert st["done_count"] == 0
    assert st["err_count"] == 0
    assert "M54629" in st["cancelled"]


def test_bug_021_frontend_stop_buttons_contract():
    """BUG-021: Giao diện HTML/CSS/JS phải hiển thị nút Dừng cả ở thanh AI chạy lại (#btn-stop-file-ai),
    cột trái (#btn-stop-batch + #batch-progress) và trên dòng file đang chạy (.btn-stop-item)."""
    html = (PROJECT_ROOT / "src" / "tomtat" / "web" / "templates" / "index.html").read_text(encoding="utf-8")
    assert 'id="btn-stop-file-ai"' in html
    assert 'id="btn-stop-batch"' in html
    assert 'id="batch-progress"' in html

    css = (PROJECT_ROOT / "src" / "tomtat" / "web" / "static" / "app.css").read_text(encoding="utf-8")
    assert ".btn-stop-ai" in css
    assert ".btn-stop-item" in css

    state_js = (PROJECT_ROOT / "src" / "tomtat" / "web" / "static" / "modules" / "state.js").read_text(encoding="utf-8")
    assert "STOPPING_STEMS" in state_js
    assert "BATCH_ACTIVE" in state_js

    sidebar_js = (PROJECT_ROOT / "src" / "tomtat" / "web" / "static" / "modules" / "sidebar.js").read_text(encoding="utf-8")
    assert "registerStopFileHandler" in sidebar_js
    assert "syncRunningProgressBar" in sidebar_js
    assert "btn-stop-item" in sidebar_js and "data-stop-stem" in sidebar_js

    editor_js = (PROJECT_ROOT / "src" / "tomtat" / "web" / "static" / "modules" / "editor.js").read_text(encoding="utf-8")
    assert "stopFileAI" in editor_js
    assert "btn-stop-file-ai" in editor_js
    assert "/stop" in editor_js
    assert "data.cancelled" in editor_js
    assert "registerStopFileHandler(stopFileAI)" in editor_js

    batch_js = (PROJECT_ROOT / "src" / "tomtat" / "web" / "static" / "modules" / "batch.js").read_text(encoding="utf-8")
    assert "syncRunningProgressBar" in batch_js
    assert "STOPPING_STEMS" in batch_js

    modals_js = (PROJECT_ROOT / "src" / "tomtat" / "web" / "static" / "modules" / "modals.js").read_text(encoding="utf-8")
    assert 'lg.status === "cancelled"' in modals_js

    app_js = (PROJECT_ROOT / "src" / "tomtat" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert "btn-stop-file-ai" in app_js
    assert "stopFileAI" in app_js
    assert "startBatchPollingFallback" in app_js


# ---------------------------------------------------------------------------
# BUG-022: Lỗi APITimeoutError ở gemini-3.8-flash và các model khác
# ---------------------------------------------------------------------------

def test_bug_022_config_and_default_timeouts_use_wide_safety_ceilings():
    """BUG-022: config.yaml mặc định đặt trần timeout an toàn rất rộng theo từng model (600/420/360/180s)
    thay cho ngưỡng 90s cũ để tránh cắt ngang mất RPD mà vẫn thoát luồng khi socket treo câm;
    đồng thời vẫn hỗ trợ đặt null/0/'none' nếu muốn bỏ hẳn client-side read timeout."""
    from types import SimpleNamespace
    from tomtat.llm import DEFAULT_TIMEOUT_S, get_model_timeout_s

    cfg = load_config()
    g = cfg["gemini"]
    assert g["timeout_s"] == 300
    assert int(g["max_timeouts_per_model"]) >= 1

    expected_by_model = {
        "gemini-4-hydrogen": 600.0,
        "gemini-3.8-flash": 420.0,
        "gemini-3.6-flash": 360.0,
        "gemini-3.5-flash-lite": 180.0,
        "gemini-3.1-flash-lite": 180.0,
    }
    chain = [g["model"]] + list(g.get("fallback_models") or [])
    for m in chain:
        assert get_model_timeout_s(m, cfg) == expected_by_model[m]
        assert DEFAULT_TIMEOUT_S[m] == expected_by_model[m]
        assert get_model_timeout_s(m, None) is None
    assert get_model_timeout_s("gemini-custom-future", cfg) == 300.0

    # Kiểm tra trực tiếp trên genai.Client thật: http_options giữ connect=30.0 & read=None làm fallback,
    # còn từng call truyền đúng trần rộng theo model vào interactions.create (và nới >600s được khi retry)
    client = GeminiClient(cfg, ["AIzaDummyTestKeyForHttpOptionsCheck1234"])
    sdk_client = client._get_or_create_client("AIzaDummyTestKeyForHttpOptionsCheck1234")
    hx_timeout = sdk_client.interactions.sdk_configuration.client.timeout
    assert hx_timeout.read is None
    assert hx_timeout.connect == 30.0
    assert sdk_client.interactions.sdk_configuration.timeout_ms is None
    assert client._compute_call_timeout_s("gemini-4-hydrogen", "s", "p", timeout_retries=1) > 600.0

    seen = []

    class FakeInteractions:
        def create(self, **kwargs):
            seen.append((kwargs["model"], kwargs["timeout"]))
            return SimpleNamespace(output_text='{"ok": true}', usage=None, status="completed")

    fake_sdk = SimpleNamespace(interactions=FakeInteractions())
    for m in chain:
        client._call_once(m, "abstract", "sys", "prompt", {}, client=fake_sdk)
    assert seen == [(m, expected_by_model[m]) for m in chain]

    # Nếu người dùng đặt null / 0 / 'none' (toàn cục hoặc cho 1 model) thì vẫn bỏ hoàn toàn read timeout
    unlimited_cfg = {
        "paths": {"work": str(cfg.work_dir)},
        "gemini": {
            "model": "gemini-4-hydrogen",
            "timeout_s": 300,
            "timeout_s_by_model": {"gemini-3.8-flash": None, "gemini-3.6-flash": 0},
        },
    }
    assert get_model_timeout_s("gemini-4-hydrogen", unlimited_cfg) == 300.0
    assert get_model_timeout_s("gemini-3.8-flash", unlimited_cfg) is None
    assert get_model_timeout_s("gemini-3.6-flash", unlimited_cfg) is None

    null_client = GeminiClient(
        {"paths": {"work": str(cfg.work_dir)}, "gemini": {"model": "gemini-3.8-flash", "timeout_s": None}},
        ["AIzaDummyTestKeyForHttpOptionsCheck1234"],
    )
    seen.clear()
    null_client._call_once("gemini-3.8-flash", "abstract", "sys", "prompt", {}, client=fake_sdk)
    assert seen == [("gemini-3.8-flash", None)]


def test_bug_022_timeout_escalates_evicts_client_and_falls_back_across_models(tmp_path, monkeypatch):
    """BUG-022: Khi cấu hình timeout cứng và gặp APITimeoutError / ContinuationTimeoutError:
    1) Tự loại bỏ client HTTP cũ khỏi cache để lượt sau mở kết nối mới sạch.
    2) Thử lại lần sau phải tự nới timeout lớn hơn (và cộng đệm với prompt rất dài).
    3) Đạt max_timeouts_per_model (kể cả có 503 xen giữa) phải trượt ngay sang model kế tiếp
       thay vì kẹt 48 key x 3 vòng; nếu mọi model đều timeout thì báo lỗi tiếng Việt rõ ràng."""
    import json
    import threading
    from types import SimpleNamespace
    import pytest
    from google.genai import errors as genai_errors
    from google.genai._gaos.lib.compat_errors import APITimeoutError
    from google.genai._interactions_continuation import ContinuationTimeoutError
    from tomtat.llm import LLMError, bind_cancel_event
    from tomtat.services import FileService

    monkeypatch.setattr("tomtat.llm._get_quota_cycle_date", lambda: "2026-01-01")
    monkeypatch.setattr("tomtat.llm.base.get_model_rpm_per_key", lambda m, c=None: 10_000)
    monkeypatch.setattr("tomtat.llm.gemini.get_model_rpm_per_key", lambda m, c=None: 10_000)

    keys = [f"key_to_{i:02d}" for i in range(1, 7)]
    monkeypatch.setattr("tomtat.config.get_api_keys", lambda root=None: keys)

    cfg = load_config()
    cfg["paths"] = {
        "input": str(tmp_path / "input"),
        "output": str(tmp_path / "output"),
        "work": str(tmp_path / "work"),
        "prompts": str(cfg.prompts_dir),
        "report": str(tmp_path / "output" / "baocao.xlsx"),
    }
    cfg["gemini"]["min_interval_s"] = 0.0
    cfg["gemini"]["rpm_limits"] = {}
    cfg["gemini"]["backoff_base_s"] = 0.0
    cfg["gemini"]["backoff_max_s"] = 0.0
    cfg["gemini"]["timeout_s"] = 180
    cfg["gemini"]["timeout_s_by_model"] = {
        "gemini-4-hydrogen": 420,
        "gemini-3.8-flash": 300,
        "gemini-3.6-flash": 300,
        "gemini-3.5-flash-lite": 180,
        "gemini-3.1-flash-lite": 180,
    }
    cfg["gemini"]["max_timeouts_per_model"] = 2

    client = GeminiClient(cfg, keys)
    created_clients: list[tuple[str, object]] = []
    seen_calls: list[tuple[str, str, float]] = []

    class Err503(Exception):
        status_code = 503

    plan: dict[tuple[str, int], str] = {}
    call_counter: dict[str, int] = {}

    class FakeGenAIClient:
        def __init__(self, key: str):
            self.key = key
            self.interactions = SimpleNamespace(
                create=self._create,
                sdk_configuration=SimpleNamespace(retry_config=None),
            )

        def _create(self, **kwargs):
            m = kwargs["model"]
            t = float(kwargs["timeout"])
            seen_calls.append((m, self.key, t))
            idx = call_counter.get(m, 0) + 1
            call_counter[m] = idx
            outcome = plan.get((m, idx), "ok")
            if outcome == "api_timeout":
                raise APITimeoutError(request=None)
            if outcome == "cont_timeout":
                inner = ContinuationTimeoutError("timed out")
                raise genai_errors.ContinuationInterruptedError("interrupted", continuation_token="tok") from inner
            if outcome == "503":
                raise Err503("high demand")
            return SimpleNamespace(output_text='{"ok": true}', usage=None, status="completed")

    monkeypatch.setattr(
        client,
        "_genai",
        SimpleNamespace(Client=lambda api_key, **kw: created_clients.append((api_key, FakeGenAIClient(api_key))) or created_clients[-1][1]),
    )

    # Kịch bản A (chạy qua luồng có cancel_ev giống Web UI):
    # - 4-hydrogen: lượt 1 APITimeoutError (420s), lượt 2 503, lượt 3 ContinuationTimeoutError -> rơi sang 3.8-flash
    # - 3.8-flash: lượt 1 APITimeoutError (timeout=300s), lượt 2 nới timeout lên 405s và thành công!
    plan.update({
        ("gemini-4-hydrogen", 1): "api_timeout",
        ("gemini-4-hydrogen", 2): "503",
        ("gemini-4-hydrogen", 3): "cont_timeout",
        ("gemini-3.8-flash", 1): "api_timeout",
        ("gemini-3.8-flash", 2): "ok",
    })
    log_dir = tmp_path / "work" / "S95434" / "logs"
    with bind_cancel_event(threading.Event()):
        res = client.generate_json(step="abstract", system="sys", prompt="short", schema={}, log_dir=log_dir, attempt=1)

    assert res.model == "gemini-3.8-flash"
    h_calls = [c for c in seen_calls if c[0] == "gemini-4-hydrogen"]
    f_calls = [c for c in seen_calls if c[0] == "gemini-3.8-flash"]
    assert len(h_calls) == 3
    assert len(f_calls) == 2
    assert h_calls[0][2] == 420.0
    assert h_calls[2][2] > h_calls[0][2]
    assert f_calls[0][2] == 300.0
    assert f_calls[1][2] > f_calls[0][2]

    # Key bị timeout phải bị loại khỏi cache _clients
    first_h_key = h_calls[0][1]
    assert first_h_key not in client._clients

    # Log phải ghi nhận timeout_s và action fallback_timeout / next_key_timeout
    logs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(log_dir.glob("*.json"))]
    assert any(lg.get("action") == "next_key_timeout" and lg.get("timeout_s") == 420.0 for lg in logs)
    assert any(lg.get("action") == "fallback_timeout" and lg.get("model") == "gemini-4-hydrogen" for lg in logs)

    # Prompt rất dài (>25.000 ký tự) phải tự cộng thêm đệm timeout (khi bật timeout cứng)
    long_timeout = client._compute_call_timeout_s("gemini-3.8-flash", "s", "x" * 45_000, timeout_retries=0)
    assert long_timeout > 300.0

    # FileService.get_file_logs tôn trọng timeout_s đã ghi trong log (ngay cả khi đã nới)
    svc = FileService(cfg)
    (log_dir / "inflight_boosted.json").write_text(
        json.dumps({
            "step": "abstract",
            "attempt": 1,
            "model": "gemini-3.8-flash",
            "timeout_s": 405.0,
            "in_progress": True,
            "phase": "calling_api",
            "started_epoch": __import__("time").time() - 360.0,
        }),
        encoding="utf-8",
    )
    inflight_entry = next(lg for lg in svc.get_file_logs("S95434")["logs"] if lg["filename"] == "inflight_boosted.json")
    assert inflight_entry["in_progress"] is True

    # Kịch bản B: Mọi model trong chain đều timeout liên tục -> dừng sạch sau 2 lần/model và báo hint tiếng Việt rõ
    seen_calls.clear()
    call_counter.clear()
    plan.clear()
    client._model_cursor.clear()
    for m in client.models_chain:
        for i in range(1, 5):
            plan[(m, i)] = "api_timeout"
    with pytest.raises(LLMError) as exc_info:
        client.generate_json(step="abstract", system="sys", prompt="short", schema={})
    assert "vượt quá thời gian chờ" in str(exc_info.value)
    for m in client.models_chain:
        assert sum(1 for c in seen_calls if c[0] == m) == 2


def test_bug_022_sync_config_updates_cached_client_on_reload(tmp_path, monkeypatch):
    """BUG-022: Khi config.yaml được reload() với timeout_s / max_timeouts_per_model / models_chain mới,
    make_client() và GeminiClient.sync_config() phải cập nhật nóng client đang cache."""
    import tomtat.services.batch_service as bs
    from tomtat.llm import get_model_timeout_s

    monkeypatch.setattr("tomtat.llm._get_quota_cycle_date", lambda: "2026-01-01")
    keys = ["key_sync_01"]
    monkeypatch.setattr("tomtat.config.get_api_keys", lambda root=None: keys)
    monkeypatch.setattr(bs, "_CACHED_CLIENT", None)
    monkeypatch.setattr(bs, "_CACHED_KEYS", [])

    cfg = load_config()
    cfg["paths"] = {"work": str(tmp_path / "work")}
    c1 = bs.make_client(cfg)
    assert c1.timeout == 300.0
    assert get_model_timeout_s("gemini-3.8-flash", c1.cfg) == 420.0
    assert c1.max_timeouts_per_model == 2

    cfg["gemini"]["timeout_s"] = 210
    cfg["gemini"]["max_timeouts_per_model"] = 3
    cfg["gemini"]["timeout_s_by_model"]["gemini-3.8-flash"] = 360
    c2 = bs.make_client(cfg)
    assert c2 is c1
    assert c2.timeout == 210.0
    assert c2.max_timeouts_per_model == 3
    assert get_model_timeout_s("gemini-3.8-flash", c2.cfg) == 360.0

    # Đổi lại về null -> tắt timeout nóng luôn
    cfg["gemini"]["timeout_s"] = None
    cfg["gemini"]["timeout_s_by_model"] = {}
    c3 = bs.make_client(cfg)
    assert c3.timeout is None
    assert get_model_timeout_s("gemini-3.8-flash", c3.cfg) is None


# ---------------------------------------------------------------------------
# BUG-023: Kiểm tra cập nhật mỗi commit GitHub khi khởi động, không báo giữa phiên
# ---------------------------------------------------------------------------

def test_bug_023_every_commit_detected_across_smart_http_bom_and_git(tmp_path, monkeypatch):
    """BUG-023: Mọi commit push lên GitHub phải được phát hiện ở thiết bị khác (cả Smart HTTP,
    REST API, version.json có UTF-8 BOM, và repo .git không bị làm bẩn version.json)."""
    import json
    import subprocess
    from tomtat.updater import _read_version_commit, get_remote_commit

    # 1. version.json có UTF-8 BOM (do PowerShell 5.1 tạo) vẫn phải đọc đúng commit
    (tmp_path / "version.json").write_bytes(
        b"\xef\xbb\xbf" + json.dumps({"commit": "1111111aaaaaaa"}).encode("utf-8")
    )
    assert _read_version_commit(tmp_path) == "1111111aaaaaaa"

    # 2. Git Smart HTTP (/info/refs?service=git-upload-pack) phân tích đúng SHA mới nhất không cần .git
    (tmp_path / ".github_repo_url").write_text("https://github.com/Bowfm/TomTatBienSoan.git", encoding="utf-8")

    def _pkt(payload: bytes) -> bytes:
        return f"{len(payload) + 4:04x}".encode("ascii") + payload

    pkt_body = (
        _pkt(b"# service=git-upload-pack\n")
        + b"0000"
        + _pkt(b"2222222222222222222222222222222222222222 HEAD\x00symref=HEAD:refs/heads/main\n")
        + _pkt(b"2222222222222222222222222222222222222222 refs/heads/main\n")
        + b"0000"
    )

    class FakeResp:
        def __init__(self, data: bytes):
            self._data = data

        def read(self) -> bytes:
            return self._data

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=5, context=None: FakeResp(pkt_body))
    rem_sha = get_remote_commit(tmp_path)
    assert rem_sha == "2222222222222222222222222222222222222222"

    res = check_update(tmp_path)
    assert res["has_update"] is True
    assert res["status"] == "update_available"
    assert res["current_commit"] == "1111111"
    assert res["latest_commit"] == "2222222"

    # 3. Khi phản hồi HTTP là HTML lỗi proxy (không phải SHA hợp lệ) -> không nhận nhầm làm commit SHA
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=5, context=None: FakeResp(b"<html>Blocked</html>"))
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert get_remote_commit(tmp_path) == ""

    # 4. Repo .git: run_update thành công qua git pull không được ghi đè bẩn version.json
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    orig_v = b'{\n  "commit": "1111111aaaaaaa"\n}'
    (tmp_path / "version.json").write_bytes(orig_v)
    monkeypatch.setattr("tomtat.updater.get_current_commit", lambda root=None: "1111111aaaaaaa")
    monkeypatch.setattr("tomtat.updater.get_remote_commit", lambda root=None: "2222222bbbbbbb")
    monkeypatch.setattr("shutil.which", lambda name: "git.exe" if name == "git" else None)

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:2] == ["git", "merge-base"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
        if cmd[:2] == ["git", "pull"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="Updating 1111111..2222222", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)
    ok, msg = run_update(tmp_path)
    assert ok is True
    assert "[Git]" in msg
    assert any(c[:2] == ["git", "pull"] for c in calls)
    assert (tmp_path / "version.json").read_bytes() == orig_v


def test_bug_023_startup_only_update_notification_ignores_mid_session_commits(tmp_path, monkeypatch):
    """BUG-023: Thông báo cập nhật chỉ kiểm tra theo phiên khởi động; nếu có commit mới trong
    khi người dùng đang dùng thì không báo giữa phiên, chỉ báo ở phiên khởi động tiếp theo."""
    from tomtat.web import create_app

    cfg = load_config()
    cfg["paths"] = {
        "input": str(tmp_path / "input"),
        "output": str(tmp_path / "output"),
        "work": str(tmp_path / "work"),
        "prompts": str(cfg.prompts_dir),
        "report": str(tmp_path / "output" / "baocao.xlsx"),
    }

    state_commits = {"cur": "aaaa1110000000", "rem": "aaaa1110000000"}
    monkeypatch.setattr("tomtat.updater.get_current_commit", lambda root=None: state_commits["cur"])
    monkeypatch.setattr("tomtat.updater.get_remote_commit", lambda root=None: state_commits["rem"])

    # Phiên 1: Khởi động khi local == GitHub -> không có thông báo cập nhật
    app1 = create_app(cfg, mock=True)
    tc1 = app1.test_client()
    r1 = tc1.get("/api/check-update").get_json()
    assert r1["has_update"] is False
    assert r1["status"] == "up_to_date"

    # Đang sử dụng phiên 1 thì có commit mới push lên GitHub -> làm mới trang vẫn KHÔNG báo
    state_commits["rem"] = "bbbb2220000000"
    r1_reload = tc1.get("/api/check-update").get_json()
    assert r1_reload["has_update"] is False
    assert r1_reload["status"] == "up_to_date"

    # Kiểm tra live thủ công (?live=1) thấy commit mới nhưng vẫn không làm lộ thông báo tự động giữa phiên
    r1_live = tc1.get("/api/check-update?live=1").get_json()
    assert r1_live["has_update"] is True
    assert r1_live["latest_commit"] == "bbbb222"
    r1_after_live = tc1.get("/api/check-update").get_json()
    assert r1_after_live["has_update"] is False

    # Phiên 2 (khởi động lại công cụ): phát hiện ngay commit mới từ GitHub
    app2 = create_app(cfg, mock=True)
    tc2 = app2.test_client()
    r2 = tc2.get("/api/check-update").get_json()
    assert r2["has_update"] is True
    assert r2["status"] == "update_available"
    assert r2["current_commit"] == "aaaa111"
    assert r2["latest_commit"] == "bbbb222"

    # Sau khi bấm cập nhật thành công -> trạng thái phiên chuyển sang up_to_date, reload không hiện lại banner
    def fake_run_update(_root=None):
        state_commits["cur"] = "bbbb2220000000"
        return True, "[GitHub Archive] Đã cập nhật thành công 3 files (phiên bản bbbb222) từ kho GitHub!"

    monkeypatch.setattr("tomtat.updater.run_update", fake_run_update)
    r_upd = tc2.post("/api/self-update", json={})
    assert r_upd.status_code == 200
    upd_data = r_upd.get_json()
    assert upd_data["ok"] is True
    assert upd_data["current_commit"] == "bbbb222"

    r2_after_upd = tc2.get("/api/check-update").get_json()
    assert r2_after_upd["has_update"] is False
    assert r2_after_upd["status"] == "up_to_date"
    assert r2_after_upd["current_commit"] == "bbbb222"


def test_bug_023_frontend_and_packaging_update_contract():
    """BUG-023: Frontend không polling cập nhật giữa phiên; kiểm tra thủ công dùng ?live=1;
    dong_goi_chia_se.bat ghi version.json vào $stage không BOM và cli cmd_review tiền kiểm tra lúc khởi động."""
    modals_js = (PROJECT_ROOT / "src" / "tomtat" / "web" / "static" / "modules" / "modals.js").read_text(encoding="utf-8")
    assert 'api("/api/check-update")' in modals_js
    assert 'api("/api/check-update?live=1")' in modals_js
    assert 'info.status === "offline"' in modals_js
    init_block = modals_js[modals_js.index("initUpdateChecker"):modals_js.index("openVersionsModal")]
    assert "setInterval" not in init_block

    pack_bat = (PROJECT_ROOT / "dong_goi_chia_se.bat").read_text(encoding="utf-8")
    assert "Join-Path $stage 'version.json'" in pack_bat
    assert "UTF8Encoding($false)" in pack_bat

    cli_py = (PROJECT_ROOT / "src" / "tomtat" / "cli.py").read_text(encoding="utf-8")
    assert "get_startup_update_info" in cli_py


# ---------------------------------------------------------------------------
# BUG-024: Tối ưu cập nhật (manifest dọn file cũ, tóm tắt commit, startup cache,
#          auto-restart khi đổi Python, config.local.yaml, guard trước khi push)
# ---------------------------------------------------------------------------

def test_bug_024_zip_update_cleans_obsolete_files_and_protects_local_config(tmp_path, monkeypatch):
    """BUG-024: Cập nhật ZIP qua run_update_detailed phải:
    1) Xóa các file mã nguồn/prompt cũ không còn trong ZIP mới (theo .updater_manifest.json).
    2) Theo dõi chính xác changed_python (True khi .py đổi/xóa, False khi chỉ sửa prompt).
    3) Bảo vệ tuyệt đối PROTECTED_ENTRIES và config.local.yaml."""
    from tomtat.updater import run_update_detailed

    (tmp_path / ".github_repo_url").write_text("https://github.com/Bowfm/TomTatBienSoan.git", encoding="utf-8")
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "user_state.json").write_text('{"keep": 1}', encoding="utf-8")
    (tmp_path / "config.local.yaml").write_text("workers: 1\nreview:\n  port: 8766\n", encoding="utf-8")

    def _make_zip(comment: bytes, files: dict[str, str]) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.comment = comment
            for rel, content in files.items():
                zf.writestr(f"TomTatBienSoan-main/{rel}", content)
        return buf.getvalue()

    class FakeResp:
        def __init__(self, data: bytes):
            self._data = data

        def read(self) -> bytes:
            return self._data

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    # Lần 1: Cài bản v1 gồm old_mod.py + keep_mod.py + prompts/old.md + config.local.yaml giả trong ZIP
    z1 = _make_zip(
        b"1111111111111111111111111111111111111111",
        {
            "src/tomtat/old_mod.py": "x = 1\n",
            "src/tomtat/keep_mod.py": "y = 1\n",
            "prompts/old.md": "old prompt\n",
            "prompts/keep.md": "keep v1\n",
            "config.local.yaml": "workers: 99\n",
        },
    )
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=30, context=None: FakeResp(z1))
    d1 = run_update_detailed(tmp_path)
    assert d1["ok"] is True
    assert d1["changed_python"] is True
    assert (tmp_path / "src" / "tomtat" / "old_mod.py").exists()
    assert (tmp_path / "prompts" / "old.md").exists()
    # config.local.yaml của máy người dùng không bị ghi đè
    assert "port: 8766" in (tmp_path / "config.local.yaml").read_text(encoding="utf-8")

    # Lần 2: Bản v2 bỏ old_mod.py và prompts/old.md, chỉ cập nhật prompts/keep.md + xóa old_mod.py
    z2 = _make_zip(
        b"2222222222222222222222222222222222222222",
        {
            "src/tomtat/keep_mod.py": "y = 1\n",
            "prompts/keep.md": "keep v2\n",
        },
    )
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=30, context=None: FakeResp(z2))
    d2 = run_update_detailed(tmp_path)
    assert d2["ok"] is True
    assert not (tmp_path / "src" / "tomtat" / "old_mod.py").exists()
    assert not (tmp_path / "prompts" / "old.md").exists()
    assert "src/tomtat/old_mod.py" in d2["removed_files"]
    assert "prompts/old.md" in d2["removed_files"]
    assert d2["changed_python"] is True  # vì old_mod.py bị xóa

    # Lần 3: Bản v3 chỉ sửa prompt (không đụng .py) -> changed_python phải là False
    z3 = _make_zip(
        b"3333333333333333333333333333333333333333",
        {
            "src/tomtat/keep_mod.py": "y = 1\n",
            "prompts/keep.md": "keep v3\n",
        },
    )
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=30, context=None: FakeResp(z3))
    d3 = run_update_detailed(tmp_path)
    assert d3["ok"] is True
    assert d3["updated"] is True
    assert d3["changed_python"] is False
    assert (tmp_path / "work" / "user_state.json").read_text(encoding="utf-8") == '{"keep": 1}'
    assert "port: 8766" in (tmp_path / "config.local.yaml").read_text(encoding="utf-8")


def test_bug_024_config_local_yaml_deep_merge_and_reload(tmp_path):
    """BUG-024: config.local.yaml phải ghi đè từng khóa lồng nhau (deep merge) lên config.yaml
    mà không làm mất các khóa khác trong cùng mục, và reload() phải nạp lại được."""
    import shutil
    shutil.copy2(PROJECT_ROOT / "config.yaml", tmp_path / "config.yaml")
    (tmp_path / "config.local.yaml").write_text(
        "workers: 1\nreview:\n  port: 8766\n",
        encoding="utf-8-sig",
    )

    cfg = load_config(root=tmp_path)
    assert cfg["workers"] == 1
    assert cfg["review"]["port"] == 8766
    assert cfg["review"]["host"] == "127.0.0.1"
    assert cfg["gemini"]["model"]

    (tmp_path / "config.local.yaml").write_text(
        "workers: 2\nreview:\n  port: 8799\n",
        encoding="utf-8",
    )
    cfg.reload()
    assert cfg["workers"] == 2
    assert cfg["review"]["port"] == 8799


def test_bug_024_update_summary_and_startup_cache_handoff(tmp_path, monkeypatch):
    """BUG-024: check_update(cache_for_startup=True, include_details=True) lấy nội dung commit mới
    và truyền cache cho server khi khởi động để không gọi mạng GitHub 2 lần liên tiếp."""
    import json
    from tomtat.web import create_app
    from tomtat.web.app import get_startup_update_info

    (tmp_path / ".github_repo_url").write_text("https://github.com/Bowfm/TomTatBienSoan.git", encoding="utf-8")
    cur_sha = "1111111111111111111111111111111111111111"
    rem_sha = "2222222222222222222222222222222222222222"
    monkeypatch.setattr("tomtat.updater.get_current_commit", lambda root=None: cur_sha)
    monkeypatch.setattr("tomtat.updater.get_remote_commit", lambda root=None: rem_sha)

    cmp_payload = json.dumps({
        "ahead_by": 2,
        "commits": [
            {"commit": {"message": "Commit 1"}},
            {"commit": {"message": "Toi uu co che cap nhat\nChi tiet dong 2"}},
        ],
    }).encode("utf-8")

    class FakeResp:
        def read(self):
            return cmp_payload

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=3, context=None: FakeResp())
    info = check_update(tmp_path, cache_for_startup=True, include_details=True)
    assert info["has_update"] is True
    assert info["ahead_by"] == 2
    assert info["latest_message"] == "Toi uu co che cap nhat"
    cache_file = tmp_path / "work" / ".startup_update_cache.json"
    assert cache_file.exists()

    # Server khởi động ngay sau đó phải tiêu thụ cache (không gọi lại check_update)
    cfg = load_config()
    cfg.root = tmp_path
    cfg["paths"] = {
        "input": str(tmp_path / "input"),
        "output": str(tmp_path / "output"),
        "work": str(tmp_path / "work"),
        "prompts": str(cfg.prompts_dir),
        "report": str(tmp_path / "output" / "baocao.xlsx"),
    }
    monkeypatch.setattr(
        "tomtat.updater.check_update",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("Không được gọi lại check_update khi đã có startup cache!")),
    )
    app = create_app(cfg, mock=True)
    got = get_startup_update_info(app)
    assert got["has_update"] is True
    assert got["latest_message"] == "Toi uu co che cap nhat"
    assert not cache_file.exists()


def test_bug_024_self_update_ai_lock_restart_flags_and_scripts(tmp_path, monkeypatch):
    """BUG-024: POST /api/self-update phải chặn 409 khi có AI đang chạy, trả restart_required /
    restarting đúng khi file Python thay đổi, và các file .bat/.gitignore/.js giữ đúng hợp đồng."""
    import threading
    from tomtat.web import create_app

    cfg = load_config()
    cfg["paths"] = {
        "input": str(tmp_path / "input"),
        "output": str(tmp_path / "output"),
        "work": str(tmp_path / "work"),
        "prompts": str(cfg.prompts_dir),
        "report": str(tmp_path / "output" / "baocao.xlsx"),
    }
    app = create_app(cfg, mock=True)
    tc = app.test_client()

    # 1. Đang chạy AI -> chặn cập nhật 409
    app.config["TOMTAT_BATCH_MGR"].running_stems.add("M54629")
    r_busy = tc.post("/api/self-update", json={})
    assert r_busy.status_code == 409
    assert r_busy.get_json()["ok"] is False
    app.config["TOMTAT_BATCH_MGR"].running_stems.clear()

    # 2. Khi testing=True và có thay đổi Python -> trả restart_required=True, không thoát tiến trình
    monkeypatch.setattr(
        "tomtat.updater.run_update_detailed",
        lambda _root=None: {
            "ok": True,
            "message": "[Git] Đã cập nhật thành công",
            "method": "git",
            "updated": True,
            "changed_python": True,
            "current_commit": "abcdef1234567",
            "removed_files": [],
        },
    )
    app.testing = True
    r_req = tc.post("/api/self-update", json={}).get_json()
    assert r_req["ok"] is True
    assert r_req["changed_python"] is True
    assert r_req["restarting"] is False
    assert r_req["restart_required"] is True

    # 3. Khi chạy qua khoi_dong.bat (TOMTAT_RESTART_ON_EXIT=1) và không ở chế độ testing -> hẹn thoát mã 42
    scheduled: list[tuple] = []

    class FakeTimer:
        def __init__(self, interval, fn, args=()):
            self.interval = interval
            self.fn = fn
            self.args = args
            self.daemon = False

        def start(self):
            scheduled.append((self.interval, self.fn, self.args))

    monkeypatch.setenv("TOMTAT_RESTART_ON_EXIT", "1")
    monkeypatch.setattr(threading, "Timer", FakeTimer)
    app.testing = False
    r_auto = tc.post("/api/self-update", json={}).get_json()
    assert r_auto["ok"] is True
    assert r_auto["restarting"] is True
    assert r_auto["restart_required"] is False
    assert scheduled and scheduled[0][2] == (42,)

    # 4. Kiểm tra hợp đồng tĩnh các file script và frontend
    kd_bat = (PROJECT_ROOT / "khoi_dong.bat").read_text(encoding="utf-8")
    assert "TOMTAT_RESTART_ON_EXIT=1" in kd_bat
    assert "errorlevel 42" in kd_bat
    assert "--no-browser" in kd_bat
    assert "cache_for_startup=True" in kd_bat

    push_bat = (PROJECT_ROOT / "day_cap_nhat.bat").read_text(encoding="utf-8")
    assert "verify_guard.py" in push_bat
    assert "pytest.exe" in push_bat
    assert "pyproject.toml" in push_bat

    pack_bat = (PROJECT_ROOT / "dong_goi_chia_se.bat").read_text(encoding="utf-8")
    assert ".updater_manifest.json" in pack_bat
    assert "pyproject.toml" in pack_bat

    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "config.local.yaml" in gitignore

    modals_js = (PROJECT_ROOT / "src" / "tomtat" / "web" / "static" / "modules" / "modals.js").read_text(encoding="utf-8")
    assert "res.restarting" in modals_js
    assert "res.restart_required" in modals_js
    assert "info.latest_message" in modals_js


# ---------------------------------------------------------------------------
# BUG-025: Rollback về phiên bản cũ bị dính lại stale_steps / stale_reason từ snapshot cũ
# ---------------------------------------------------------------------------

def test_bug_025_rollback_clears_stale_steps_and_stale_reason(tmp_path, monkeypatch):
    """BUG-025: Khi rollback về bất kỳ version cũ nào (kể cả version từng được tạo sau ai:extract / ai:label
    có stale_steps + stale_reason), state sau rollback và version restore mới tuyệt đối không được hiện
    cảnh báo stale ('Bản trích chọn vừa được AI tạo lại...')."""
    from tomtat.llm import MockClient
    from tomtat.pipeline import process_file
    from tomtat.state import FileWorkspace
    from tomtat.web import create_app

    cfg, inp = _setup_version_test_env(tmp_path, monkeypatch)
    client = MockClient(cfg)

    # 1. Tạo v001 sạch, sau đó chạy lại chỉ bước extract -> tạo v002 có stale_steps=['abstract']
    process_file(inp, cfg, client, force=True, restart=True)
    process_file(inp, cfg, client, only_step="extract")
    ws = FileWorkspace(cfg.work_dir, inp)
    v2 = ws.get_draft_version(2)
    assert v2 is not None
    assert v2["source"] == "ai:extract"
    assert v2["stale_steps"] == ["abstract"]
    assert "Bản trích chọn" in (v2.get("stale_reason") or "")

    # 2. Chạy tiếp bước abstract -> tạo v003 sạch stale_steps
    process_file(inp, cfg, client, only_step="abstract")
    assert ws.load_state()["stale_steps"] == []

    app = create_app(cfg, mock=True)
    tc = app.test_client()

    # 3. Rollback về v002 (từng có stale_steps=['abstract']) -> stale_steps & stale_reason phải trống hoàn toàn
    r_rb1 = tc.post("/api/file/M54629/restore", json={"version": 2})
    assert r_rb1.status_code == 200
    d_rb1 = r_rb1.get_json()
    assert d_rb1["ok"] is True
    assert d_rb1["restored_from"] == 2
    assert d_rb1["state"]["stale_steps"] == []
    assert d_rb1["state"]["stale_reason"] is None
    v4 = d_rb1["versions"][0]
    assert v4["source"] == "restore"
    assert v4["stale_steps"] == []
    assert v4["stale_reason"] is None

    # 4. Nếu state hiện tại đang có stale_steps (do đổi phong cách) rồi rollback về v001 -> cũng phải sạch stale
    st_now = ws.load_state()
    r_chg = tc.post(
        "/api/file/M54629/save",
        json={
            "style": "chính luận" if st_now["label"]["value"] != "chính luận" else "khoa học",
            "extract": st_now["extract"]["text"],
            "abstract": st_now["abstract"]["text"],
            "auto_save": True,
        },
    )
    assert r_chg.status_code == 200
    assert r_chg.get_json()["state"]["stale_steps"]

    r_rb2 = tc.post("/api/file/M54629/restore", json={"version": 1})
    assert r_rb2.status_code == 200
    d_rb2 = r_rb2.get_json()
    assert d_rb2["state"]["stale_steps"] == []
    assert d_rb2["state"]["stale_reason"] is None





