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


# ---------------------------------------------------------------------------
# BUG-026: Chỉnh sửa phong cách thủ công giữ nguyên ghi chú giải thích phong cách cũ
# ---------------------------------------------------------------------------

def test_bug_026_manual_style_edit_updates_label_reason_and_preserves_through_rerun(tmp_path, monkeypatch):
    """BUG-026: Khi người duyệt chỉnh sửa phong cách thủ công (và chạy lại Trích chọn & Biên soạn),
    phần ghi chú giải thích (`label.reason`), `confidence`, `per_doc`, `model_used` và cờ cảnh báo AI
    phải cập nhật đồng bộ theo phong cách mới, không giữ nguyên giải thích của phong cách cũ;
    đồng thời vẫn bảo toàn `model_label`/`ai_confidence` cho `learned_memory.json` và khôi phục được khi quay lại nhãn AI."""
    from tomtat.llm import MockClient
    from tomtat.memory import load_memory
    from tomtat.pipeline import LABEL_REVIEW_FLAG, process_file
    from tomtat.state import FileWorkspace
    from tomtat.web import create_app

    cfg, inp = _setup_version_test_env(tmp_path, monkeypatch)
    orig_style = "báo chí - công luận"
    alt_style = "khoa học"
    old_ai_reason = "Văn bản thuộc phong cách báo chí - công luận do mang hình thức bài tường thuật sự kiện."

    class CustomLabelClient(MockClient):
        def _mock_label(self, ctx, attempt):
            data = ctx["data"]
            per = [
                {"doc": d["n"], "label": orig_style, "reason": f"VB {d['n']} là bài báo tường thuật."}
                for d in data["docs"]
            ]
            return {
                "label": orig_style,
                "per_doc": per,
                "confidence": "trung_binh",
                "reason": old_ai_reason,
            }

    ai_client = CustomLabelClient(cfg)
    out1 = process_file(inp, cfg, ai_client, force=True, restart=True)
    ws = FileWorkspace(cfg.work_dir, inp)
    st1 = out1["state"]
    assert st1["label"]["value"] == orig_style
    assert st1["label"]["reason"] == old_ai_reason
    assert st1["label"]["confidence"] == "trung_binh"
    assert st1["label"]["model_used"] == "mock"
    assert LABEL_REVIEW_FLAG in st1["flags"]

    app = create_app(cfg, mock=True)
    monkeypatch.setattr("tomtat.web.routes.process.make_client", lambda c, force_mock=False: CustomLabelClient(c))
    tc = app.test_client()

    # 1. Đổi phong cách thủ công qua /save (auto_save=True) -> reason/confidence/model_used/per_doc/flags phải cập nhật ngay
    r_save = tc.post(
        "/api/file/M54629/save",
        json={
            "style": alt_style,
            "extract": st1["extract"]["text"],
            "abstract": st1["abstract"]["text"],
            "auto_save": True,
        },
    )
    assert r_save.status_code == 200
    lb_manual = r_save.get_json()["state"]["label"]
    assert lb_manual["value"] == alt_style
    assert lb_manual["manual_edited"] is True
    assert lb_manual["confidence"] == "thu_cong"
    assert lb_manual["model_used"] is None
    assert lb_manual["per_doc"] == []
    assert old_ai_reason not in lb_manual["reason"]
    assert alt_style in lb_manual["reason"]
    assert orig_style in lb_manual["reason"]
    assert lb_manual["model_label"] == orig_style
    assert lb_manual["ai_confidence"] == "trung_binh"
    assert LABEL_REVIEW_FLAG not in r_save.get_json()["state"]["flags"]

    # 2. Chạy lại cả 2 bản trích chọn & biên soạn (extract_abstract) -> tạo version mới và KHÔNG khôi phục reason/model cũ
    r_rerun = tc.post(
        "/api/file/M54629/rerun",
        json={
            "step": "extract_abstract",
            "style": alt_style,
            "mock": True,
            "ignore_mismatch": True,
        },
    )
    assert r_rerun.status_code == 200
    d_rerun = r_rerun.get_json()
    assert d_rerun["versions"][0]["source"] == "ai:extract_abstract"
    lb_after = d_rerun["state"]["label"]
    assert lb_after["value"] == alt_style
    assert lb_after["manual_edited"] is True
    assert lb_after["confidence"] == "thu_cong"
    assert lb_after["model_used"] is None
    assert lb_after["per_doc"] == []
    assert old_ai_reason not in lb_after["reason"]
    assert alt_style in lb_after["reason"]
    assert LABEL_REVIEW_FLAG not in d_rerun["state"]["flags"]

    # 3. GET /api/file/<stem> không được backfill nhầm model_used='mock' từ log cũ lên label khi đã manual_edited
    d_detail = tc.get("/api/file/M54629").get_json()
    assert d_detail["state"]["label"]["model_used"] is None
    assert d_detail["state"]["label"]["reason"] == lb_after["reason"]

    # 4. Đổi ngược về phong cách ban đầu của AI -> khôi phục đúng reason, confidence, per_doc, model_used & flag AI gốc
    r_revert = tc.post(
        "/api/file/M54629/save",
        json={
            "style": orig_style,
            "extract": d_rerun["state"]["extract"]["text"],
            "abstract": d_rerun["state"]["abstract"]["text"],
            "auto_save": True,
        },
    )
    assert r_revert.status_code == 200
    d_rev = r_revert.get_json()
    assert d_rev["state"]["label"]["value"] == orig_style
    assert d_rev["state"]["label"]["manual_edited"] is False
    assert d_rev["state"]["label"]["reason"] == old_ai_reason
    assert d_rev["state"]["label"]["confidence"] == "trung_binh"
    assert d_rev["state"]["label"]["model_used"] == "mock"
    assert len(d_rev["state"]["label"]["per_doc"]) == len(st1["label"]["per_doc"])
    assert LABEL_REVIEW_FLAG in d_rev["state"]["flags"]

    # 5. Gửi trực tiếp /rerun extract_abstract với style mới (không qua /save trước) -> cũng phải đồng bộ label
    r_direct = tc.post(
        "/api/file/M54629/rerun",
        json={
            "step": "extract_abstract",
            "style": alt_style,
            "mock": True,
            "ignore_mismatch": True,
        },
    )
    assert r_direct.status_code == 200
    lb_dir = r_direct.get_json()["state"]["label"]
    assert lb_dir["value"] == alt_style
    assert lb_dir["manual_edited"] is True
    assert old_ai_reason not in lb_dir["reason"]
    assert alt_style in lb_dir["reason"]

    # 6. process_file(..., restart=False) phải tôn trọng nhãn thủ công (confidence='thu_cong'), không chạy đè step_label
    spy_resume = CustomLabelClient(cfg)
    out_resume = process_file(inp, cfg, spy_resume, force=True, restart=False)
    assert "label" not in spy_resume.calls
    assert out_resume["state"]["label"]["value"] == alt_style
    assert out_resume["state"]["label"]["manual_edited"] is True

    # 7. Duyệt & xuất vẫn ghi nhận được vào learned_memory (AI đoán orig_style -> người duyệt chốt alt_style)
    d_cur = tc.get("/api/file/M54629").get_json()["state"]
    r_app = tc.post(
        "/api/file/M54629/approve",
        json={
            "style": alt_style,
            "extract": d_cur["extract"]["text"],
            "abstract": d_cur["abstract"]["text"],
            "confirm_warnings": True,
        },
    )
    assert r_app.status_code == 200
    mem = load_memory(cfg.work_dir)
    assert any(
        c["stem"] == "M54629" and c["ai_guessed"] == orig_style and c["human_approved"] == alt_style
        for c in mem["label_corrections"]
    )

    # 8. State cũ trên đĩa từng bị lệch (value != model_label nhưng thiếu manual_edited) khi GET /api/file/<stem> phải tự sửa hiển thị
    st_legacy = ws.load_state()
    st_legacy["label"] = {
        "value": alt_style,
        "model_label": orig_style,
        "computed_majority": orig_style,
        "per_doc": st1["label"]["per_doc"],
        "confidence": "trung_binh",
        "reason": old_ai_reason,
        "model_used": "gemini-4-hydrogen",
    }
    st_legacy["flags"] = [LABEL_REVIEW_FLAG]
    ws.save_state(st_legacy)
    d_healed = tc.get("/api/file/M54629").get_json()["state"]
    assert d_healed["label"]["manual_edited"] is True
    assert d_healed["label"]["confidence"] == "thu_cong"
    assert d_healed["label"]["model_used"] is None
    assert old_ai_reason not in d_healed["label"]["reason"]
    assert alt_style in d_healed["label"]["reason"]
    assert LABEL_REVIEW_FLAG not in d_healed["flags"]

    # 9. Chạy AI: Gán nhãn lại (only_step='label') phải khôi phục chế độ AI (manual_edited=False) và cập nhật reason mới từ AI
    r_relabel = tc.post("/api/file/M54629/rerun", json={"step": "label", "mock": True})
    assert r_relabel.status_code == 200
    lb_rel = r_relabel.get_json()["state"]["label"]
    assert lb_rel["value"] == orig_style
    assert lb_rel["manual_edited"] is False
    assert lb_rel["reason"] == old_ai_reason
    assert lb_rel["confidence"] == "trung_binh"


def test_bug_026_frontend_label_sync_contract():
    """BUG-026: Frontend phải cập nhật ngay phần giải thích, độ chắc chắn, model badge và flags khi autoSaveSilent sau khi đổi phong cách."""
    editor_js = (PROJECT_ROOT / "src" / "tomtat" / "web" / "static" / "modules" / "editor.js").read_text(encoding="utf-8")
    assert "renderLabelSection" in editor_js
    assert "renderSummaryMeta" in editor_js
    assert "thu_cong" in editor_js
    assert "refreshLabel" in editor_js
    assert "store.CURRENT_STATE.flags = data.state.flags || []" in editor_js


# ---------------------------------------------------------------------------
# BUG-027: Nâng lên 3 luồng an toàn, tiết kiệm RPD khi bão 503 và chống 429 QPS
# ---------------------------------------------------------------------------

def test_bug_027_three_workers_safe_and_pacing_contract(tmp_path, monkeypatch):
    """BUG-027: Cấu hình mặc định 3 luồng phải đồng bộ giữa config.yaml, UI, pool scaling, BatchManager
    và bảo đảm 3 luồng chạy song song không bao giờ vượt RPM / min_interval_s trên mỗi key."""
    import threading
    from tomtat.config import clamp_workers, get_pool_scaling
    from tomtat.llm.base import get_model_min_interval_s, get_model_rpm_per_key

    # 1. Hợp đồng config.yaml & giao diện UI
    cfg = load_config()
    assert cfg.get("workers") == 3
    assert cfg.get("max_workers") == 3
    g = cfg.get("gemini") or {}
    assert int(g.get("max_transient_per_model", 0)) >= 6
    assert int(g.get("model_outage_threshold", 0)) >= 3
    assert float(g.get("model_outage_cooldown_s", 0)) >= 10.0
    assert float(g.get("global_min_dispatch_interval_s", 0)) >= 1.0
    assert float(g.get("qps_global_cooldown_s", 0)) >= 5.0

    html = (PROJECT_ROOT / "src" / "tomtat" / "web" / "templates" / "index.html").read_text(encoding="utf-8")
    sel = re.search(r'<select id="sel-concurrency".*?</select>', html, re.S)
    assert sel is not None
    vals = [int(v) for v in re.findall(r'<option value="(\d+)"', sel.group(0))]
    assert vals == [1, 2, 3]

    # 2. Pool scaling với 58 keys -> khuyến nghị đúng 3 luồng, kẹp mọi mức vượt trần
    keys_58 = [f"k58_{i:02d}" for i in range(1, 59)]
    monkeypatch.setattr("tomtat.config.get_api_keys", lambda root=None: keys_58)
    sc = get_pool_scaling(cfg, api_keys=keys_58)
    assert sc["recommended_workers"] == 3
    assert sc["max_workers"] == 3
    assert sc["concurrency_options"] == [1, 2, 3]
    assert sc["max_concurrent_calls"] >= 5
    assert clamp_workers(16, cfg, api_keys=keys_58) == 3

    # 3. BatchManager chạy đúng 3 file song song
    cfg_batch = _make_batch_cfg(tmp_path, monkeypatch, max_workers=3)
    cfg_batch["workers"] = 3
    workers, peak = _run_batch_and_measure(cfg_batch, monkeypatch, [f"F{i}" for i in range(7)], "auto")
    assert workers == 3
    assert peak == 3

    # 4. 3 luồng cùng đặt chỗ key đồng thời không bao giờ vượt RPM / min_interval_s
    monkeypatch.setattr("tomtat.llm._get_quota_cycle_date", lambda: "2026-01-01")
    keys_3 = ["ka", "kb", "kc"]
    cfg_rpm = load_config()
    cfg_rpm["paths"] = {"work": str(tmp_path / "work_rpm")}
    client = GeminiClient(cfg_rpm, keys_3)
    monkeypatch.setattr(client, "_get_or_create_client", lambda k: object())
    monkeypatch.setattr(client, "_save_quota_state_unlocked", lambda: None)

    for model in ("gemini-4-hydrogen", "gemini-3.5-flash-lite"):
        reserved: dict[str, list[float]] = {k: [] for k in keys_3}
        lock = threading.Lock()
        barrier = threading.Barrier(3)

        def _acq():
            barrier.wait()
            for _ in range(30):
                k, _c, _w, ts = client._acquire_paced_key_client_with_ts(model)
                assert k is not None
                with lock:
                    reserved[k].append(ts)

        threads = [threading.Thread(target=_acq) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        min_iv = get_model_min_interval_s(model, cfg_rpm)
        rpm = get_model_rpm_per_key(model, cfg_rpm)
        assert sum(len(v) for v in reserved.values()) == 90
        for k, ts_list in reserved.items():
            ts_list.sort()
            for a, b in zip(ts_list, ts_list[1:]):
                assert b - a >= min_iv - 1e-6
            for i, t0 in enumerate(ts_list):
                assert sum(1 for t in ts_list[i:] if t < t0 + 60.0) <= rpm


def test_bug_027_503_circuit_breaker_and_outage_cooldown_saves_rpd(tmp_path, monkeypatch):
    """BUG-027: Khi gặp bão 503 trên model chính với pool lớn (58 keys) và 3 luồng song song,
    mạch bảo vệ + cooldown hạ nhiệt model phải chặn lãng phí RPD, trượt êm sang model dự phòng
    và tự khôi phục lại model chính sau khi hết cooldown."""
    import threading
    import time as _time
    from tomtat.llm import get_rpd_status

    monkeypatch.setattr("tomtat.llm._get_quota_cycle_date", lambda: "2026-01-01")
    monkeypatch.setattr("tomtat.llm.base.get_model_rpm_per_key", lambda m, c=None: 10_000)
    monkeypatch.setattr("tomtat.llm.gemini.get_model_rpm_per_key", lambda m, c=None: 10_000)

    keys = [f"key_rpd_{i:02d}" for i in range(1, 59)]
    monkeypatch.setattr("tomtat.config.get_api_keys", lambda root=None: keys)

    cfg = load_config()
    cfg["paths"] = {"work": str(tmp_path / "work")}
    cfg["gemini"]["min_interval_s"] = 0.0
    cfg["gemini"]["rpm_limits"] = {}
    cfg["gemini"]["backoff_base_s"] = 0.05
    cfg["gemini"]["backoff_max_s"] = 0.6
    cfg["gemini"]["max_transient_per_model"] = 8
    cfg["gemini"]["model_outage_threshold"] = 5
    cfg["gemini"]["model_outage_cooldown_s"] = 1.2
    cfg["gemini"]["global_min_dispatch_interval_s"] = 0.0
    cfg["gemini"]["qps_global_cooldown_s"] = 0.0

    client = GeminiClient(cfg, keys)
    hydrogen_state = {"mode": "503"}
    calls: list[str] = []
    calls_lock = threading.Lock()

    class Err503(Exception):
        status_code = 503

    monkeypatch.setattr(client, "_get_or_create_client", lambda k: object())

    def fake_call_once(model_name, step, system, prompt, schema, client=None):
        with calls_lock:
            calls.append(model_name)
        if model_name == "gemini-4-hydrogen" and hydrogen_state["mode"] == "503":
            raise Err503("high demand")
        return '{"ok": true}', {}, "completed"

    monkeypatch.setattr(client, "_call_once", fake_call_once)

    # 1. 3 luồng chạy đồng thời trong cơn bão 503 trên gemini-4-hydrogen
    barrier = threading.Barrier(3)
    results = []

    def _run(idx: int):
        barrier.wait()
        res = client.generate_json(
            step="abstract",
            system="sys",
            prompt=f"prompt_{idx}",
            schema={},
            log_dir=tmp_path / "work" / f"F{idx}" / "logs",
            attempt=1,
        )
        results.append(res.model)

    threads = [threading.Thread(target=_run, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    assert len(results) == 3
    assert all(m == "gemini-3.8-flash" for m in results)
    h_wasted = calls.count("gemini-4-hydrogen")
    # Không bao giờ đốt tới 3 x 174 = 522 hay 3 x 8 = 24 RPD; cả 3 luồng cộng lại chỉ tốn ~5-8 RPD
    assert 5 <= h_wasted <= 12

    # 2. Lượt gọi ngay sau trong lúc 4-hydrogen đang hạ nhiệt (1.2s) phải bỏ qua 4-hydrogen (0 RPD lãng phí thêm)
    calls.clear()
    res_during = client.generate_json(step="abstract", system="sys", prompt="during_cd", schema={})
    assert res_during.model == "gemini-3.8-flash"
    assert calls.count("gemini-4-hydrogen") == 0

    # 3. Sau khi hết cooldown 1.2s và 4-hydrogen ổn định lại -> tự quay lại ưu tiên 4-hydrogen
    _time.sleep(1.25)
    hydrogen_state["mode"] = "ok"
    calls.clear()
    res_rec = client.generate_json(step="abstract", system="sys", prompt="recovered", schema={})
    assert res_rec.model == "gemini-4-hydrogen"
    assert calls == ["gemini-4-hydrogen"]

    rpd_map = {x["model"]: x for x in get_rpd_status(cfg, api_keys=keys)}
    assert rpd_map["gemini-4-hydrogen"]["used"] == h_wasted + 1


def test_bug_027_anti_qps_stagger_shared_cooldown_and_retry_differentiation(tmp_path, monkeypatch):
    """BUG-027: Giãn nhịp toàn cục chống 3 luồng bắn burst đồng thời; khi 1 luồng chạm 429 QPS,
    luồng khác tự tôn trọng cooldown chung, đồng thời lượt thử lại tự biến đổi nhẹ payload để thoát dedup."""
    import json
    import threading
    import time as _time

    monkeypatch.setattr("tomtat.llm._get_quota_cycle_date", lambda: "2026-01-01")
    monkeypatch.setattr("tomtat.llm.base.get_model_rpm_per_key", lambda m, c=None: 10_000)
    monkeypatch.setattr("tomtat.llm.gemini.get_model_rpm_per_key", lambda m, c=None: 10_000)

    keys = [f"key_qps_{i:02d}" for i in range(1, 7)]
    monkeypatch.setattr("tomtat.config.get_api_keys", lambda root=None: keys)

    cfg = load_config()
    cfg["paths"] = {"work": str(tmp_path / "work")}
    cfg["gemini"]["min_interval_s"] = 0.0
    cfg["gemini"]["rpm_limits"] = {}
    cfg["gemini"]["backoff_base_s"] = 0.12
    cfg["gemini"]["backoff_max_s"] = 1.0
    cfg["gemini"]["global_min_dispatch_interval_s"] = 0.08
    cfg["gemini"]["qps_global_cooldown_s"] = 0.20

    client = GeminiClient(cfg, keys)
    monkeypatch.setattr(client, "_get_or_create_client", lambda k: object())

    # 1. Đặt chỗ giãn nhịp toàn cục: thread có RPM wait dài (13s) không được chặn thread khác chạy ngay
    w_long = client._reserve_gateway_wait(min_wait_s=13.0)
    w_now = client._reserve_gateway_wait(min_wait_s=0.0)
    assert w_long >= 12.9
    assert w_now == 0.0
    client._dispatch_slots.clear()

    # 2. 3 luồng chạy đồng thời được giãn nhịp >= global_min_dispatch_interval_s
    dispatch_times: list[float] = []
    dt_lock = threading.Lock()

    def ok_call(model_name, step, system, prompt, schema, client=None):
        with dt_lock:
            dispatch_times.append(_time.monotonic())
        return '{"ok": true}', {}, "completed"

    monkeypatch.setattr(client, "_call_once", ok_call)
    barrier = threading.Barrier(3)
    threads = [
        threading.Thread(
            target=lambda idx=i: (
                barrier.wait(),
                client.generate_json(step="label", system="sys", prompt=f"doc_{idx}", schema={}),
            )
        )
        for i in range(3)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)
    dispatch_times.sort()
    assert len(dispatch_times) == 3
    assert dispatch_times[1] - dispatch_times[0] >= 0.06
    assert dispatch_times[2] - dispatch_times[1] >= 0.06

    # 3. Khi 1 luồng gặp 429 QPS:
    #    - Đẩy _global_qps_next_ts để luồng khác không nối đuôi bắn vào cổng đang nghẽn
    #    - Lượt retry thêm dấu hiệu phân biệt ở prompt gửi đi nhưng giữ nguyên prompt trong log
    seen_prompts: list[tuple[str, float]] = []
    qps_triggered = threading.Event()

    class ErrQPS(Exception):
        status_code = 429

    def qps_then_ok(model_name, step, system, prompt, schema, client=None):
        seen_prompts.append((prompt, _time.monotonic()))
        if prompt == "doc_A":
            qps_triggered.set()
            raise ErrQPS(
                "Error code: 429 - {'error': {'message': 'The query-per-second (QPS) limit for identical or "
                "near-identical request payloads has been exceeded.', 'code': 'too_many_requests'}}"
            )
        return '{"ok": true}', {}, "completed"

    client._dispatch_slots.clear()
    client._global_qps_next_ts = 0.0
    monkeypatch.setattr(client, "_call_once", qps_then_ok)
    log_dir_a = tmp_path / "work" / "A" / "logs"

    t_b_box: list[float] = []
    t_a = threading.Thread(
        target=lambda: client.generate_json(step="label", system="sys", prompt="doc_A", schema={}, log_dir=log_dir_a),
    )

    def _run_b():
        assert qps_triggered.wait(timeout=2.0)
        _time.sleep(0.02)
        t_start_b = _time.monotonic()
        client.generate_json(step="label", system="sys", prompt="doc_B", schema={})
        t_b_box.append(_time.monotonic() - t_start_b)

    t_b = threading.Thread(target=_run_b)
    t_a.start()
    t_b.start()
    t_a.join(timeout=10.0)
    t_b.join(timeout=10.0)

    # doc_A lượt đầu giữ nguyên, lượt thử lại có nhãn phân biệt để thoát QPS dedup
    a_prompts = [p for p, _ in seen_prompts if p.startswith("doc_A")]
    assert len(a_prompts) == 2
    assert a_prompts[0] == "doc_A"
    assert "[Phiên thử lại #" in a_prompts[1]
    # doc_B phải chờ cooldown cổng chung chứ không bắn tức thì
    assert t_b_box and t_b_box[0] >= 0.12
    # Log giữ nguyên prompt sạch, không khóa key hay trừ RPD
    logs_a = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(log_dir_a.glob("*.json"))]
    assert logs_a[0]["prompt"] == "doc_A"
    assert logs_a[0]["action"] == "backoff_payload_qps"
    assert len(client._exhausted) == 0


def test_bug_027_memory_and_semaphore_thread_safety(tmp_path):
    """BUG-027: Khóa semaphore toàn cục và memory.py phải an toàn khi gọi đồng thời từ nhiều luồng."""
    import threading
    from tomtat.llm.base import BaseClient
    from tomtat.memory import load_memory, record_approved_sample

    # 1. ensure_concurrency_limit đồng thời
    errors: list[Exception] = []
    barrier = threading.Barrier(4)

    def _bump(n: int):
        try:
            barrier.wait()
            for _ in range(20):
                BaseClient.ensure_concurrency_limit(n)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    ths = [threading.Thread(target=_bump, args=(i + 5,)) for i in range(4)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    assert errors == []
    assert BaseClient._sem_limit >= 8

    # 2. record_approved_sample đồng thời không làm mất dữ liệu / hỏng JSON
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    b2 = threading.Barrier(3)

    def _save_mem(i: int):
        b2.wait()
        units = {
            "file": f"S{i}.docx",
            "docs": [{"n": 1, "units": [{"id": 1, "text": f"Nội dung mẫu {i}."}]}],
            "budget": {"total_words": 100},
        }
        st = {
            "stem": f"S{i}",
            "label": {"value": "khoa học", "model_label": "báo chí - công luận", "confidence": "thap"},
            "abstract": {"text": f"Tóm tắt {i}."},
        }
        record_approved_sample(work_dir, units, st, "khoa học", f"Trích {i}.", f"Biên soạn {i}.")

    m_ths = [threading.Thread(target=_save_mem, args=(i,)) for i in range(3)]
    for t in m_ths:
        t.start()
    for t in m_ths:
        t.join()
    mem = load_memory(work_dir)
    assert mem["total_approved_learned"] == 3
    assert len(mem["label_corrections"]) == 3


# ---------------------------------------------------------------------------
# BUG-028: Bản biên soạn xuất hiện ký tự `\n` viết thô (như bài S89777)
# ---------------------------------------------------------------------------

def test_bug_028_unescape_and_detect_literal_escapes():
    """BUG-028: unescape_escaped_newlines, find_literal_escapes và clean_abstract_text phải
    chuyển sạch `\\n` / `\\r\\n` / `\\t` viết thô thành xuống dòng thực sự ở cả đơn văn bản lẫn đa văn bản,
    nhưng không làm hỏng đường dẫn Windows hay ký hiệu vốn có trong văn bản gốc."""
    from tomtat.checks import clean_abstract_text, clean_multi_doc_abstract
    from tomtat.textutil import find_literal_escapes, unescape_escaped_newlines

    # 1. Trường hợp thực tế S89777: LLM double-escape `\\n` giữa các đoạn trong JSON
    raw_s89777 = (
        "Chẩn đoán và điều trị sớm giúp cải thiện tỷ lệ tử vong do bệnh tim mạch."
        r"\nTheo khuyến cáo của Hội Tim mạch học châu Âu, bác sĩ chẩn đoán tăng huyết áp."
        r"\nTăng huyết áp gồm 2 nhóm chính."
        r"\nĐa số trường hợp không có triệu chứng."
    )
    assert find_literal_escapes(raw_s89777) == [r"\n"]
    cleaned_s = clean_abstract_text(raw_s89777, n_docs=1)
    assert r"\n" not in cleaned_s
    assert cleaned_s.splitlines() == [
        "Chẩn đoán và điều trị sớm giúp cải thiện tỷ lệ tử vong do bệnh tim mạch.",
        "Theo khuyến cáo của Hội Tim mạch học châu Âu, bác sĩ chẩn đoán tăng huyết áp.",
        "Tăng huyết áp gồm 2 nhóm chính.",
        "Đa số trường hợp không có triệu chứng.",
    ]
    assert find_literal_escapes(cleaned_s) == []

    # 2. Hỗ trợ \\r\\n, \\n\\n, \\t, khoảng trắng quanh \\n và tiền tố tiếng Việt kết thúc bằng 'A:'
    mixed = "Mục A:" + r"\n" + "Đoạn 1. " + r"\r\n\t" + "Đoạn 2." + r"\n\n" + "Đoạn 3."
    assert set(find_literal_escapes(mixed)) >= {r"\n", r"\r\n", r"\t"}
    cleaned_mixed = unescape_escaped_newlines(mixed)
    assert "\\" not in cleaned_mixed
    assert cleaned_mixed == "Mục A:\nĐoạn 1.\nĐoạn 2.\n\nĐoạn 3."

    # 3. Đa văn bản vừa có `\\n` viết thô vừa có cụm dẫn dắt cấm dùng
    raw_multi = (
        "Bài một nêu kết quả tăng trưởng 18,5%."
        r"\nTrong chia sẻ khác, bài hai cảnh báo nguy cơ dịch bệnh."
    )
    cleaned_multi = clean_multi_doc_abstract(raw_multi, n_docs=2)
    assert r"\n" not in cleaned_multi
    assert "\n\n" in cleaned_multi
    assert "Trong chia sẻ khác" not in cleaned_multi
    assert cleaned_multi == "Bài một nêu kết quả tăng trưởng 18,5%.\n\nBài hai cảnh báo nguy cơ dịch bệnh."

    # 4. Bảo toàn đường dẫn Windows thực và token vốn có trong văn bản gốc
    win_src = r"Xem đường dẫn C:\new\test.txt và ký hiệu \nabla trong công thức."
    win_abs = r"Đọc file C:\new\test.txt và ký hiệu \nabla.\nTiếp theo kiểm tra kết quả."
    assert find_literal_escapes(win_abs, preserve_source=win_src) == [r"\n"]
    fixed_win = unescape_escaped_newlines(win_abs, preserve_source=win_src)
    assert r"C:\new\test.txt" in fixed_win
    assert r"\nabla" in fixed_win
    assert r"\nTiếp" not in fixed_win
    assert fixed_win == "Đọc file C:\\new\\test.txt và ký hiệu \\nabla.\nTiếp theo kiểm tra kết quả."


def test_bug_028_checks_pipeline_and_prompt_prevent_literal_newline_export(tmp_path, make_docx):
    """BUG-028:
    1) run_checks phải báo ERROR `literal_escape` và chặn approve/export nếu bản biên soạn còn `\\n`.
    2) Pipeline (step_abstract) phải tự làm sạch `\\n` của LLM ở cả đơn văn bản lẫn đa văn bản và xuất .docx thành nhiều đoạn sạch.
    3) process_file(..., restart=False) phải phát hiện state cũ còn `\\n` là không hợp lệ và tự sửa lại.
    4) Prompt & JSON schema không được hướng dẫn gây nhầm thành chuỗi `\\n` theo nghĩa đen.
    """
    from tomtat.checks import run_checks
    from tomtat.docx_io import load_table, row_text
    from tomtat.llm import MockClient
    from tomtat.pipeline import process_file
    from tomtat.prompts import ABSTRACT_SCHEMA
    from tomtat.services.file_service import FileService
    from tomtat.state import FileWorkspace

    cfg = load_config()
    cfg["paths"] = {
        "input": str(tmp_path / "input"),
        "output": str(tmp_path / "output"),
        "work": str(tmp_path / "work"),
        "prompts": str(cfg.prompts_dir),
        "report": str(tmp_path / "output" / "baocao.xlsx"),
    }

    # Tạo file đơn văn bản S89777 mẫu đủ dài
    (tmp_path / "input").mkdir(parents=True, exist_ok=True)
    sents = [
        f"Bệnh tăng huyết áp là vấn đề sức khỏe phổ biến lần thứ {i} cần đo huyết áp định kỳ tại cơ sở y tế."
        for i in range(1, 13)
    ]
    inp = make_docx(
        tmp_path / "input" / "S89777.docx",
        ["", " ".join(sents), "AI extract", "AI abstract"],
    )

    class EscapedNewlineMockClient(MockClient):
        def _mock_label(self, ctx, attempt):
            return {
                "label": "khoa học",
                "per_doc": [{"doc": 1, "label": "khoa học", "reason": "Y khoa"}],
                "confidence": "cao",
                "reason": "Khoa học y khoa",
            }

        def _mock_abstract(self, ctx, attempt):
            return {
                "abstract": (
                    "Bệnh tăng huyết áp là vấn đề sức khỏe phổ biến lần thứ 1 cần đo huyết áp định kỳ."
                    r"\nBệnh tăng huyết áp là vấn đề sức khỏe phổ biến lần thứ 2 cần đo huyết áp định kỳ."
                ),
                "note": "Trả về có chủ đích ký tự \\n để kiểm thử cơ chế tự làm sạch.",
            }

    # 1. Kiểm tra trực tiếp run_checks + approve_and_export chặn `\\n` chưa sửa
    svc = FileService(cfg)
    _, _, units = svc.inspect_file(inp)
    bad_abs = (
        "Bệnh tăng huyết áp là vấn đề sức khỏe phổ biến lần thứ 1 cần đo huyết áp định kỳ."
        r"\nBệnh tăng huyết áp là vấn đề sức khỏe phổ biến lần thứ 2 cần đo huyết áp định kỳ."
    )
    chk_bad = run_checks(units, "khoa học", units["docs"][0]["units"][0]["text"] + " " + units["docs"][0]["units"][1]["text"] + " " + units["docs"][0]["units"][2]["text"] + " " + units["docs"][0]["units"][3]["text"], bad_abs, cfg)
    assert any(i.code == "literal_escape" and i.level == "error" for i in chk_bad.errors)

    # 2. Pipeline chạy với LLM trả về `\\n` -> tự làm sạch thành xuống dòng thật, không lỗi, xuất .docx sạch
    out = process_file(inp, cfg, EscapedNewlineMockClient(cfg), force=True, restart=True)
    st = out["state"]
    assert st["checks"]["ok"] is True
    assert r"\n" not in st["abstract"]["text"]
    assert "\n" in st["abstract"]["text"]

    app_res = svc.approve_and_export(
        "S89777",
        style=st["label"]["value"],
        extract=st["extract"]["text"],
        abstract=st["abstract"]["text"],
        confirmed_warnings=True,
    )
    assert app_res["ok"] is True
    out_docx = tmp_path / "output" / "S89777.docx"
    _, _, rows = load_table(out_docx)
    assert r"\n" not in row_text(rows[-1])
    assert len([ln for ln in rows[-1] if ln.strip()]) == 2

    # Nếu cố ý save_draft có `\\n` -> approve_and_export bắt buộc từ chối
    svc.save_draft("S89777", st["label"]["value"], st["extract"]["text"], bad_abs)
    blocked = svc.approve_and_export(
        "S89777",
        style=st["label"]["value"],
        extract=st["extract"]["text"],
        abstract=bad_abs,
        confirmed_warnings=True,
    )
    assert blocked["ok"] is False
    assert any(i["code"] == "literal_escape" for i in blocked["checks"]["issues"])

    # 3. Nếu state cũ trên đĩa còn dính `\\n`, process_file(..., restart=False) phải tự nhận ra lỗi và sửa lại
    ws = FileWorkspace(cfg.work_dir, inp)
    st_dirty = ws.load_state()
    st_dirty["abstract"]["text"] = bad_abs
    st_dirty["status"] = "da_co_nhap"
    ws.save_state(st_dirty)
    resumed = process_file(inp, cfg, EscapedNewlineMockClient(cfg), force=True, restart=False)
    assert r"\n" not in resumed["state"]["abstract"]["text"]
    assert resumed["state"]["checks"]["ok"] is True

    # 4. Kiểm tra hợp đồng prompt & schema
    abs_prompt = (PROJECT_ROOT / "prompts" / "abstract.md").read_text(encoding="utf-8")
    assert "KHÔNG viết hai ký tự `\\n` theo nghĩa đen" in abs_prompt
    assert "KHÔNG viết chuỗi ký tự `\\n\\n` theo nghĩa đen" in abs_prompt
    desc = ABSTRACT_SCHEMA["properties"]["abstract"]["description"]
    assert "các đoạn cách nhau bằng \\n" not in desc
    assert "xuống dòng thực sự" in desc


# ---------------------------------------------------------------------------
# BUG-029: Bấm 1 trong 5 nút AI khi đang chạy đủ số luồng cho phép phải chờ lượt, không tạo luồng thứ N+1
# ---------------------------------------------------------------------------

def test_bug_029_single_file_rerun_waits_when_batch_full_and_never_creates_extra_thread(tmp_path, monkeypatch):
    """BUG-029: Khi batch đang chạy đủ 3/3 luồng, bấm 1 trong 5 nút AI (async hoặc sync) ở file đang xem
    không được tạo luồng thứ 4 mà phải xếp hàng chờ; ngay khi 1 trong 3 luồng kia xong thì đến lượt ngay
    (trước các file chưa chạy của batch)."""
    import threading
    import time as _time
    from pathlib import Path
    import tomtat.services.batch_service as bs
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
    cfg["workers"] = 3
    cfg["max_workers"] = 3
    monkeypatch.setattr(cfg, "reload", lambda: None)
    monkeypatch.setattr("tomtat.config.get_api_keys", lambda root=None: [f"k{i}" for i in range(10)])

    sample_bytes = (cfg.root / "samples" / "M54629.docx").read_bytes()
    (tmp_path / "input").mkdir(parents=True, exist_ok=True)
    all_stems = ["F1", "F2", "F3", "F4", "R1", "R2"]
    for s in all_stems:
        (tmp_path / "input" / f"{s}.docx").write_bytes(sample_bytes)

    state_lock = threading.Lock()
    active_stems: set[str] = set()
    start_order: list[str] = []
    peak_active = {"n": 0}
    gates: dict[str, threading.Event] = {s: threading.Event() for s in all_stems}
    entered: dict[str, threading.Event] = {s: threading.Event() for s in all_stems}
    # Cho R1, R2, F4 chạy nhanh sau khi được cấp slot; giữ F1, F2, F3 bằng gate
    for fast_s in ("F4", "R1", "R2"):
        gates[fast_s].set()

    def fake_process_file(docx_path, cfg_, client, **kw):
        stem = Path(docx_path).stem
        with state_lock:
            active_stems.add(stem)
            start_order.append(stem)
            peak_active["n"] = max(peak_active["n"], len(active_stems))
        entered[stem].set()
        gates[stem].wait(timeout=5.0)
        ws = FileWorkspace(cfg_.work_dir, Path(docx_path))
        st = ws.load_state()
        st["status"] = "da_co_nhap"
        st["label"] = {"value": "khoa học", "confidence": "cao"}
        st["extract"] = {"text": "Câu 1.", "words": 2, "selections": {"1": [1]}}
        st["abstract"] = {"text": "Tóm tắt.", "words": 2}
        ws.save_state(st)
        with state_lock:
            active_stems.discard(stem)
        return {"skipped": False, "state": st}

    monkeypatch.setattr(bs, "process_file", fake_process_file)
    monkeypatch.setattr(bs, "write_reports", lambda c: None)
    monkeypatch.setattr("tomtat.web.routes.process.make_client", lambda _c, force_mock=False: object())

    # FileService.process gọi thẳng pipeline.process_file -> patch cả tomtat.pipeline.process_file
    import tomtat.pipeline as pl_mod
    monkeypatch.setattr(pl_mod, "process_file", fake_process_file)

    app = create_app(cfg, mock=True)
    mgr = app.config["TOMTAT_BATCH_MGR"]
    mgr._client_factory = lambda _c, force_mock=False: object()
    tc = app.test_client()

    # 1. Khởi động batch 4 file (3 luồng chạy song song: F1, F2, F3; F4 chờ trong batch)
    r_start = tc.post("/api/batch/start", json={"stems": ["F1", "F2", "F3", "F4"], "concurrency": "auto", "mock": True})
    assert r_start.status_code == 200 and r_start.get_json()["ok"] is True
    for s in ("F1", "F2", "F3"):
        assert entered[s].wait(timeout=3.0), f"{s} không chạy"

    with state_lock:
        assert len(active_stems) == 3

    # 2. Đang chạy đủ 3/3 luồng -> bấm nút AI ở R1 (async=True) và R2 (sync trong thread)
    r_q1 = tc.post("/api/file/R1/rerun", json={"step": "abstract", "async": True, "mock": True})
    assert r_q1.status_code == 202
    d_q1 = r_q1.get_json()
    assert d_q1["ok"] is True
    assert d_q1["accepted"] is True
    assert d_q1["queued"] is True
    assert d_q1["queue_position"] == 1
    assert d_q1["active_workers"] == 3
    assert d_q1["allowed_workers"] == 3

    sync_res: dict = {}

    def _run_sync_r2():
        resp = tc.post("/api/file/R2/rerun", json={"step": "label", "mock": True})
        sync_res["status_code"] = resp.status_code
        sync_res["body"] = resp.get_json()

    th_r2 = threading.Thread(target=_run_sync_r2, daemon=True)
    th_r2.start()

    # Đợi R2 vào hàng chờ
    deadline = _time.time() + 3.0
    bst = {}
    while _time.time() < deadline:
        bst = tc.get("/api/batch/status").get_json()
        if bst.get("queued_rerun_stems") == ["R1", "R2"]:
            break
        _time.sleep(0.02)

    assert bst["queued_rerun_stems"] == ["R1", "R2"]
    assert bst["queued_rerun_positions"] == {"R1": 1, "R2": 2}
    assert bst["active_worker_count"] == 3
    assert bst["allowed_workers"] == 3

    # Tuyệt đối không tạo luồng thứ 4 -> R1, R2, F4 chưa được phép chạy
    _time.sleep(0.15)
    assert not entered["R1"].is_set()
    assert not entered["R2"].is_set()
    assert not entered["F4"].is_set()
    assert peak_active["n"] == 3

    # /api/files, /api/file/R1/logs và khóa sửa/lưu/rollback/duyệt (409) đều phản ánh trạng thái chờ lượt
    files_info = tc.get("/api/files").get_json()
    assert files_info["queued_rerun_stems"] == ["R1", "R2"]
    assert files_info["queued_rerun_positions"] == {"R1": 1, "R2": 2}
    logs_r1 = tc.get("/api/file/R1/logs").get_json()
    assert "R1" in logs_r1["queued_rerun_stems"]
    assert tc.post("/api/file/R1/save", json={"style": "khoa học", "extract": "x", "abstract": "y"}).status_code == 409
    assert tc.post("/api/file/R1/restore", json={"version": 1}).status_code == 409
    assert tc.post("/api/file/R1/approve", json={"style": "khoa học", "extract": "x", "abstract": "y"}).status_code == 409
    # Bấm trùng lại nút AI trên chính file R1 đang chờ cũng phải bị chặn 409
    assert tc.post("/api/file/R1/rerun", json={"step": "abstract", "async": True, "mock": True}).status_code == 409

    # 3. Khi F1 xong -> R1 phải lập tức lấy slot vừa trống (trước F4 của batch), tổng luồng vẫn <= 3
    gates["F1"].set()
    assert entered["R1"].wait(timeout=3.0), "R1 không chạy ngay khi F1 hoàn thành"
    assert not entered["F4"].is_set(), "F4 không được chen trước R1 đang đợi"
    assert peak_active["n"] == 3

    # 4. Khi F2 xong -> R2 lấy slot tiếp theo, hoàn tất thread sync
    gates["F2"].set()
    assert entered["R2"].wait(timeout=3.0), "R2 không chạy ngay khi F2 hoàn thành"
    th_r2.join(timeout=3.0)
    assert not th_r2.is_alive()
    assert sync_res["status_code"] == 200
    assert sync_res["body"]["ok"] is True
    assert peak_active["n"] == 3

    # 5. Mở F3 -> F4 chạy và toàn bộ batch + rerun hoàn tất sạch sẽ, đỉnh luồng luôn == 3
    gates["F3"].set()
    assert entered["F4"].wait(timeout=3.0)
    deadline = _time.time() + 5.0
    while _time.time() < deadline:
        bst = tc.get("/api/batch/status").get_json()
        if not bst["active"] and not bst["all_running"] and not bst["queued_rerun_stems"]:
            break
        _time.sleep(0.02)

    assert not bst["active"]
    assert bst["done_count"] == 4
    assert bst["err_count"] == 0
    assert peak_active["n"] == 3
    assert bst["recent_rerun_results"]["R1"]["ok"] is True
    assert bst["recent_rerun_results"]["R2"]["ok"] is True
    assert start_order.index("R1") < start_order.index("F4")
    assert start_order.index("R2") < start_order.index("F4")


def test_bug_029_multiple_single_reruns_and_explicit_batch_concurrency_respected(tmp_path, monkeypatch):
    """BUG-029:
    1) Không có batch: bấm AI trên 5 file cùng lúc vẫn chỉ chạy tối đa 3 luồng (workers=3), file 4-5 phải chờ.
    2) Batch chạy với concurrency=2 (dù config cho phép 3): rerun file đơn lẻ cũng chỉ tối đa 2 luồng và chờ lượt.
    3) Bấm nút AI trên file vốn đang nằm trong hàng đợi batch (_batch_pending) không gây lỗi đụng độ batch.
    4) 2 rerun đang chạy sẵn rồi mới bấm Chạy batch -> tổng luồng batch + rerun vẫn <= 3.
    """
    import threading
    import time as _time
    from pathlib import Path
    import tomtat.pipeline as pl_mod
    import tomtat.services.batch_service as bs
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
    cfg["workers"] = 3
    cfg["max_workers"] = 3
    monkeypatch.setattr(cfg, "reload", lambda: None)
    monkeypatch.setattr("tomtat.config.get_api_keys", lambda root=None: [f"k{i}" for i in range(10)])

    sample_bytes = (cfg.root / "samples" / "M54629.docx").read_bytes()
    (tmp_path / "input").mkdir(parents=True, exist_ok=True)
    for s in ("S1", "S2", "S3", "S4", "S5", "B1", "B2", "B3", "B4"):
        (tmp_path / "input" / f"{s}.docx").write_bytes(sample_bytes)

    lock = threading.Lock()
    tracker = {"cur": 0, "peak": 0}
    b_gates: dict[str, threading.Event] = {}
    b_entered: dict[str, threading.Event] = {}

    def slow_process(docx_path, cfg_, client, **kw):
        stem = Path(docx_path).stem
        with lock:
            tracker["cur"] += 1
            tracker["peak"] = max(tracker["peak"], tracker["cur"])
        if stem in b_entered:
            b_entered[stem].set()
        if stem in b_gates:
            b_gates[stem].wait(timeout=5.0)
        else:
            _time.sleep(0.12)
        ws = FileWorkspace(cfg_.work_dir, Path(docx_path))
        st = ws.load_state()
        st["status"] = "da_co_nhap"
        st["label"] = {"value": "khoa học", "confidence": "cao"}
        st["extract"] = {"text": "Câu 1.", "words": 2, "selections": {"1": [1]}}
        st["abstract"] = {"text": "Tóm tắt.", "words": 2}
        ws.save_state(st)
        with lock:
            tracker["cur"] -= 1
        return {"skipped": False, "state": st}

    monkeypatch.setattr(bs, "process_file", slow_process)
    monkeypatch.setattr(pl_mod, "process_file", slow_process)
    monkeypatch.setattr(bs, "write_reports", lambda c: None)
    monkeypatch.setattr("tomtat.web.routes.process.make_client", lambda _c, force_mock=False: object())

    app = create_app(cfg, mock=True)
    mgr = app.config["TOMTAT_BATCH_MGR"]
    mgr._client_factory = lambda _c, force_mock=False: object()
    tc = app.test_client()

    # 1. Không chạy batch, gửi 5 rerun đồng thời -> peak <= 3 (đạt đúng 3, không bao giờ 4-5)
    for s in ("S1", "S2", "S3", "S4", "S5"):
        r = tc.post(f"/api/file/{s}/rerun", json={"step": "abstract", "async": True, "mock": True})
        assert r.status_code == 202
        if s in ("S4", "S5"):
            assert r.get_json()["queued"] is True

    deadline = _time.time() + 6.0
    while _time.time() < deadline:
        st = tc.get("/api/batch/status").get_json()
        if not st["all_running"] and not st["queued_rerun_stems"]:
            break
        _time.sleep(0.03)
    assert tracker["peak"] == 3
    for s in ("S1", "S2", "S3", "S4", "S5"):
        assert st["recent_rerun_results"][s]["ok"] is True

    # 2. Batch chạy với concurrency=2 (khi max_workers=3) + rerun file B4 vốn đang nằm trong hàng đợi batch
    tracker["cur"] = 0
    tracker["peak"] = 0
    b_gates.clear()
    b_entered.clear()
    for s in ("B1", "B2", "B3", "B4", "S1"):
        b_gates[s] = threading.Event()
        b_entered[s] = threading.Event()
    b_gates["B3"].set()
    b_gates["B4"].set()
    b_gates["S1"].set()

    r_b = tc.post("/api/batch/start", json={"stems": ["B1", "B2", "B3", "B4"], "concurrency": 2, "mock": True})
    assert r_b.get_json()["ok"] is True
    assert b_entered["B1"].wait(timeout=3.0)
    assert b_entered["B2"].wait(timeout=3.0)

    # S1 và B4 (đang chờ trong batch) bấm rerun -> cả 2 phải vào hàng chờ với allowed_workers=2
    r_s1 = tc.post("/api/file/S1/rerun", json={"step": "label", "async": True, "mock": True}).get_json()
    r_b4 = tc.post("/api/file/B4/rerun", json={"step": "all", "async": True, "mock": True}).get_json()
    assert r_s1["queued"] is True and r_s1["allowed_workers"] == 2
    assert r_b4["queued"] is True and r_b4["allowed_workers"] == 2
    _time.sleep(0.1)
    assert tracker["peak"] == 2, f"Batch concurrency=2 nhưng lại chạy {tracker['peak']} luồng!"

    b_gates["B1"].set()
    b_gates["B2"].set()
    deadline = _time.time() + 6.0
    while _time.time() < deadline:
        st2 = tc.get("/api/batch/status").get_json()
        if not st2["active"] and not st2["all_running"] and not st2["queued_rerun_stems"]:
            break
        _time.sleep(0.03)
    assert tracker["peak"] == 2
    assert st2["done_count"] == 4
    assert st2["err_count"] == 0

    # 3. 2 rerun đang chạy sẵn -> bấm batch 3 file -> tổng luồng đồng thời <= 3
    tracker["cur"] = 0
    tracker["peak"] = 0
    b_gates.clear()
    b_entered.clear()
    for s in ("S1", "S2", "B1"):
        b_gates[s] = threading.Event()
        b_entered[s] = threading.Event()
    for s in ("B2", "B3"):
        b_gates[s] = threading.Event()
        b_entered[s] = threading.Event()
        b_gates[s].set()

    assert tc.post("/api/file/S1/rerun", json={"step": "abstract", "async": True, "mock": True}).get_json()["queued"] is False
    assert tc.post("/api/file/S2/rerun", json={"step": "abstract", "async": True, "mock": True}).get_json()["queued"] is False
    assert b_entered["S1"].wait(timeout=3.0)
    assert b_entered["S2"].wait(timeout=3.0)

    tc.post("/api/batch/start", json={"stems": ["B1", "B2", "B3"], "concurrency": 3, "mock": True})
    assert b_entered["B1"].wait(timeout=3.0)
    _time.sleep(0.12)
    assert not b_entered["B2"].is_set(), "B2 không được chạy khi S1, S2, B1 đã đủ 3 luồng"
    assert tracker["peak"] == 3

    b_gates["B1"].set()
    b_gates["S1"].set()
    b_gates["S2"].set()
    deadline = _time.time() + 6.0
    while _time.time() < deadline:
        st3 = tc.get("/api/batch/status").get_json()
        if not st3["active"] and not st3["all_running"] and not st3["queued_rerun_stems"]:
            break
        _time.sleep(0.03)
    assert tracker["peak"] == 3
    assert st3["done_count"] == 3 and st3["err_count"] == 0


def test_bug_029_cancel_queued_rerun_and_frontend_queue_contract(tmp_path, monkeypatch):
    """BUG-029: Bấm Dừng AI khi file đang chờ lượt trong hàng chờ phải hủy ngay không gọi AI,
    và frontend HTML/CSS/JS có đầy đủ trạng thái hàng chờ & xử lý bất đồng bộ."""
    import threading
    import time as _time
    from pathlib import Path
    import tomtat.pipeline as pl_mod
    import tomtat.services.batch_service as bs
    from tomtat.web import create_app

    cfg = load_config()
    cfg["paths"] = {
        "input": str(tmp_path / "input"),
        "output": str(tmp_path / "output"),
        "work": str(tmp_path / "work"),
        "prompts": str(cfg.prompts_dir),
        "report": str(tmp_path / "output" / "baocao.xlsx"),
    }
    cfg["workers"] = 1
    cfg["max_workers"] = 1
    monkeypatch.setattr(cfg, "reload", lambda: None)

    sample_bytes = (cfg.root / "samples" / "M54629.docx").read_bytes()
    (tmp_path / "input").mkdir(parents=True, exist_ok=True)
    for s in ("F1", "Q1", "Q2"):
        (tmp_path / "input" / f"{s}.docx").write_bytes(sample_bytes)

    f1_entered = threading.Event()
    f1_release = threading.Event()
    called_stems: list[str] = []

    def fake_proc(docx_path, cfg_, client, **kw):
        stem = Path(docx_path).stem
        called_stems.append(stem)
        if stem == "F1":
            f1_entered.set()
            f1_release.wait(timeout=5.0)
        return {"skipped": False, "state": {"status": "da_co_nhap"}}

    monkeypatch.setattr(bs, "process_file", fake_proc)
    monkeypatch.setattr(pl_mod, "process_file", fake_proc)
    monkeypatch.setattr(bs, "write_reports", lambda c: None)
    monkeypatch.setattr("tomtat.web.routes.process.make_client", lambda _c, force_mock=False: object())

    app = create_app(cfg, mock=True)
    mgr = app.config["TOMTAT_BATCH_MGR"]
    mgr._client_factory = lambda _c, force_mock=False: object()
    tc = app.test_client()

    # 1. F1 chiếm trọn 1/1 slot -> Q1 (#1), Q2 (#2) vào hàng chờ
    tc.post("/api/batch/start", json={"stems": ["F1"], "concurrency": 1, "mock": True})
    assert f1_entered.wait(timeout=3.0)
    r_q1 = tc.post("/api/file/Q1/rerun", json={"step": "abstract", "async": True, "mock": True}).get_json()
    r_q2 = tc.post("/api/file/Q2/rerun", json={"step": "label", "async": True, "mock": True}).get_json()
    assert r_q1["queued"] is True and r_q1["queue_position"] == 1
    assert r_q2["queued"] is True and r_q2["queue_position"] == 2

    # 2. Hủy Q1 khi đang chờ -> Q1 thoát hàng chờ ngay, Q2 lên #1, không gọi AI cho Q1
    r_stop_q1 = tc.post("/api/file/Q1/stop").get_json()
    assert r_stop_q1["stopped"] is True
    assert r_stop_q1["was_queued"] is True

    bst = tc.get("/api/batch/status").get_json()
    assert bst["queued_rerun_stems"] == ["Q2"]
    assert bst["queued_rerun_positions"] == {"Q2": 1}

    # 3. Hủy toàn bộ bằng /api/batch/stop -> Q2 cũng được hủy khỏi hàng chờ, giải phóng F1
    r_stop_all = tc.post("/api/batch/stop").get_json()
    assert "Q2" in r_stop_all["stopped_stems"]
    f1_release.set()

    deadline = _time.time() + 4.0
    while _time.time() < deadline:
        bst = tc.get("/api/batch/status").get_json()
        if not bst["active"] and not bst["all_running"] and not bst["queued_rerun_stems"]:
            break
        _time.sleep(0.02)
    assert called_stems == ["F1"], f"Không được gọi AI cho file đã hủy trong hàng chờ: {called_stems}"

    # 4. Hợp đồng frontend
    state_js = (PROJECT_ROOT / "src" / "tomtat" / "web" / "static" / "modules" / "state.js").read_text(encoding="utf-8")
    assert "QUEUED_STEMS" in state_js and "isStemBusy" in state_js
    sidebar_js = (PROJECT_ROOT / "src" / "tomtat" / "web" / "static" / "modules" / "sidebar.js").read_text(encoding="utf-8")
    assert "st-queued" in sidebar_js and "Chờ lượt" in sidebar_js
    editor_js = (PROJECT_ROOT / "src" / "tomtat" / "web" / "static" / "modules" / "editor.js").read_text(encoding="utf-8")
    assert "async: true" in editor_js and "Hủy chờ AI" in editor_js and "st-queued" in editor_js
    batch_js = (PROJECT_ROOT / "src" / "tomtat" / "web" / "static" / "modules" / "batch.js").read_text(encoding="utf-8")
    assert "recent_rerun_results" in batch_js and "queued_rerun_stems" in batch_js
    modals_js = (PROJECT_ROOT / "src" / "tomtat" / "web" / "static" / "modules" / "modals.js").read_text(encoding="utf-8")
    assert "logs-queue-banner" in modals_js
    css = (PROJECT_ROOT / "src" / "tomtat" / "web" / "static" / "app.css").read_text(encoding="utf-8")
    assert ".st-queued" in css


# ---------------------------------------------------------------------------
# BUG-030: Ấn vào file ở thanh bên trái làm danh sách tự cuộn xuống dưới ngay lập tức
# ---------------------------------------------------------------------------

def test_bug_030_sidebar_click_preserves_scroll_contract_and_behavior():
    """BUG-030: Ấn chọn file ở cột bên trái (đặc biệt khi zoom trình duyệt < 100%) không được
    phá hủy DOM danh sách hay làm nhảy vị trí cuộn; renderFileList/renderDoneList/renderDocsUnits
    phải giữ nguyên vị trí cuộn và app.css tắt overflow-anchor ở các vùng cuộn động."""
    import shutil
    import subprocess

    css = (PROJECT_ROOT / "src" / "tomtat" / "web" / "static" / "app.css").read_text(encoding="utf-8")
    for cls_name in (".file-list", ".docs-container", ".versions-list"):
        block = re.search(rf"{re.escape(cls_name)}\s*\{{[^}}]*\}}", css, re.S)
        assert block is not None, f"Thiếu block CSS {cls_name}"
        assert "overflow-anchor: none" in block.group(0), (
            f"{cls_name} phải có 'overflow-anchor: none' để tránh Chromium scroll jump khi zoom != 100%"
        )

    utils_js = (PROJECT_ROOT / "src" / "tomtat" / "web" / "static" / "modules" / "utils.js").read_text(encoding="utf-8")
    assert "export function withPreservedScroll" in utils_js

    sidebar_js = (PROJECT_ROOT / "src" / "tomtat" / "web" / "static" / "modules" / "sidebar.js").read_text(encoding="utf-8")
    assert "export function syncActiveFileItem" in sidebar_js
    assert "withPreservedScroll(ul" in sidebar_js
    assert "withPreservedScroll(doneUl" in sidebar_js

    editor_js = (PROJECT_ROOT / "src" / "tomtat" / "web" / "static" / "modules" / "editor.js").read_text(encoding="utf-8")
    assert "syncActiveFileItem" in editor_js
    assert "withPreservedScroll" in editor_js
    assert "if (!syncActiveFileItem())" in editor_js

    node_exe = shutil.which("node")
    if not node_exe:
        return

    modules_dir = (PROJECT_ROOT / "src" / "tomtat" / "web" / "static" / "modules").as_uri()
    script = f"""
import assert from "node:assert/strict";

function makeClassList(el) {{
  const set = new Set();
  return {{
    add: (...cs) => cs.forEach((c) => set.add(c)),
    remove: (...cs) => cs.forEach((c) => set.delete(c)),
    toggle: (c, force) => {{
      if (force === undefined) {{
        if (set.has(c)) {{ set.delete(c); return false; }}
        set.add(c); return true;
      }}
      if (force) set.add(c); else set.delete(c);
      return Boolean(force);
    }},
    contains: (c) => set.has(c),
    _syncFromStr: (s) => {{
      set.clear();
      String(s || "").split(/\\s+/).filter(Boolean).forEach((c) => set.add(c));
    }},
  }};
}}

function makeEl(id = "") {{
  let _innerHTML = "";
  let _className = "";
  const el = {{
    id,
    dataset: {{}},
    style: {{}},
    children: [],
    value: "",
    textContent: "",
    disabled: false,
    scrollTop: 0,
    scrollLeft: 0,
    innerHTMLClears: 0,
    classList: null,
    get className() {{ return _className; }},
    set className(v) {{ _className = String(v || ""); el.classList._syncFromStr(_className); }},
    get innerHTML() {{ return _innerHTML; }},
    set innerHTML(v) {{
      _innerHTML = String(v || "");
      if (_innerHTML === "") {{
        el.innerHTMLClears += 1;
        el.children = [];
        // Mô phỏng trình duyệt reset scrollTop về 0 khi container bị xóa rỗng
        el.scrollTop = 0;
        el.scrollLeft = 0;
      }}
    }},
    appendChild(child) {{ el.children.push(child); return child; }},
    addEventListener() {{}},
    querySelector(sel) {{
      if (sel === ".btn-stop-item") return null;
      if (sel === ".btn-del-item") return makeEl();
      if (sel === "input") return makeEl();
      return null;
    }},
    querySelectorAll(sel) {{
      if (sel === ".file-item[data-stem]") {{
        return el.children.filter((c) => c.dataset && c.dataset.stem);
      }}
      return [];
    }},
  }};
  el.classList = makeClassList(el);
  return el;
}}

const els = new Map();
for (const id of [
  "file-list", "done-list", "filter-status", "cnt-total", "cnt-draft", "cnt-unprocessed",
  "cnt-approved", "badge-pending", "done-count", "cnt-unprocessed-wrap", "btn-clear-pending",
  "btn-clear-approved", "empty-state", "editor-pane", "cur-filename", "cur-meta", "cur-status",
  "flags-box", "label-stale-prompt", "query-box", "budget-hint", "ai-extract-ref",
  "ai-abstract-ref", "model-badge-label", "model-badge-extract", "model-badge-abstract",
  "sel-style", "label-conf", "label-reason", "label-per-doc", "txt-extract", "txt-abstract",
  "version-note", "docs-container", "m-total", "m-ext", "ext-word-inline", "m-compress",
  "m-abs", "abs-word-inline", "m-abs-target", "abs-target-inline", "m-ratio-label", "m-ratio",
  "m-edit-cov", "m-rouge2", "m-bart", "m-geval", "verbatim-bar", "issues-list", "btn-approve",
  "judge-card", "judge-content", "versions-list", "cur-version-badge", "version-dirty-badge",
  "versions-count", "versions-count-btn", "btn-save", "btn-rollback-prev", "btn-stop-file-ai",
  "batch-progress", "btn-stop-batch", "btn-run-pending"
]) {{
  els.set(id, makeEl(id));
}}

globalThis.document = {{
  getElementById: (id) => els.get(id) || null,
  createElement: () => makeEl(),
  querySelectorAll: () => [],
  activeElement: null,
}};

const filePayloads = {{}};
for (let i = 1; i <= 30; i++) {{
  const stem = `S895${{String(i).padStart(2, "0")}}`;
  filePayloads[stem] = {{
    state: {{
      stem, file: `${{stem}}.docx`, status: "chua_xu_ly", status_vi: "Chưa xử lý",
      label: {{ value: "", reason: "" }}, extract: {{ text: "", words: 0, selections: {{}} }},
      abstract: {{ text: "", words: 0 }}, checks: {{ n_errors: 0, n_warnings: 0, metrics: {{}}, issues: [] }},
      flags: [], stale_steps: [],
    }},
    units: {{
      query: "q",
      budget: {{ total_words: 100, extract_min: 10, extract_max: 50, extract_target_low: 20, extract_target_high: 40, extract_target_mid: 30 }},
      docs: [{{ n: 1, row: 1, words: 100, units: [{{ id: 1, para: 1, words: 10, text: "Câu 1." }}] }}],
      ai_extract: "", ai_abstract: "",
    }},
    versions: [],
  }};
}}

globalThis.fetch = async (url) => {{
  const m = String(url).match(/\\/api\\/file\\/([^/]+)$/);
  const payload = m ? filePayloads[decodeURIComponent(m[1])] : null;
  return {{ ok: Boolean(payload), status: payload ? 200 : 404, json: async () => payload || {{}} }};
}};

const {{ store }} = await import("{modules_dir}/state.js");
const {{ renderFileList }} = await import("{modules_dir}/sidebar.js");
const {{ selectFile, renderDocsUnits }} = await import("{modules_dir}/editor.js");

store.FILES = Object.keys(filePayloads).map((s) => ({{
  file: `${{s}}.docx`, status: "chua_xu_ly", status_vi: "Chưa xử lý", label: "", n_errors: 0, n_warnings: 0,
}}));
store.CURRENT_STEM = "S89520";
renderFileList();

const ul = els.get("file-list");
const origNode19 = ul.children.find((li) => li.dataset.stem === "S89519");
assert.ok(origNode19);
ul.scrollTop = 1850;
ul.scrollLeft = 12;
const clearsBeforeSelect = ul.innerHTMLClears;

await selectFile("S89519");
assert.equal(ul.innerHTMLClears, clearsBeforeSelect, "selectFile không được xóa/tạo lại DOM #file-list");
assert.equal(ul.scrollTop, 1850);
assert.equal(ul.scrollLeft, 12);
assert.equal(ul.children.find((li) => li.dataset.stem === "S89519"), origNode19);
assert.equal(origNode19.classList.contains("active"), true);
assert.equal(ul.children.find((li) => li.dataset.stem === "S89520").classList.contains("active"), false);

// Ngay cả khi renderFileList() chạy lại từ SSE/loadFileList và innerHTML='' reset scrollTop=0 -> phải khôi phục lại 1850
renderFileList();
assert.equal(ul.scrollTop, 1850);
assert.equal(ul.scrollLeft, 12);

// renderDocsUnits với preserveScroll=true giữ cuộn trên cùng file, nhưng chuyển file mới phải reset về 0
const docsBox = els.get("docs-container");
docsBox.scrollTop = 420;
renderDocsUnits({{ preserveScroll: true }});
assert.equal(docsBox.scrollTop, 420);
await selectFile("S89518");
assert.equal(docsBox.scrollTop, 0);
"""
    res = subprocess.run(
        [node_exe, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    assert res.returncode == 0, f"Node test thất bại:\nSTDOUT: {res.stdout}\nSTDERR: {res.stderr}"


# ---------------------------------------------------------------------------
# BUG-031: _reserve_gateway_wait cộng jitter sau vòng lặp làm slot đè lên nhau
# ---------------------------------------------------------------------------

def test_bug_031_reserve_gateway_wait_jitter_never_collides_with_existing_slots(tmp_path, monkeypatch):
    """BUG-031: Jitter trong _reserve_gateway_wait không bao giờ được đẩy mốc đặt chỗ mới
    đè vào khoảng < global_min_dispatch_interval_s của một slot đã đặt trước đó."""
    import random as _random

    monkeypatch.setattr("tomtat.llm._get_quota_cycle_date", lambda: "2026-01-01")
    keys = [f"key_gw_{i:02d}" for i in range(1, 5)]
    cfg = load_config()
    cfg["paths"] = {"work": str(tmp_path / "work")}
    cfg["gemini"]["backoff_base_s"] = 0.12
    cfg["gemini"]["backoff_max_s"] = 1.0
    cfg["gemini"]["global_min_dispatch_interval_s"] = 0.08
    cfg["gemini"]["qps_global_cooldown_s"] = 0.0

    client = GeminiClient(cfg, keys)
    base_now = 1_000_000.0
    monkeypatch.setattr("tomtat.llm.gemini.time.time", lambda: base_now)

    # Tái hiện đúng kịch bản gây lỗi ở BUG-027:
    # - Slot 0 ở base_now
    # - Slot 1 va chạm Slot 0 -> nhảy sang base_now + 0.08 + jitter (ví dụ +0.20 = base_now + 0.28)
    # - Thread 2 gọi tại base_now với jitter 0.20 (nếu cộng jitter sau vòng lặp sẽ thành
    #   base_now + 0.08 + 0.20 = base_now + 0.28, trùng hệt Slot 1!)
    monkeypatch.setattr(_random, "uniform", lambda a, b: 0.20 if b >= 0.20 else b)
    client._dispatch_slots = [base_now, base_now + 0.28]

    wait_s = client._reserve_gateway_wait(min_wait_s=0.0)
    assert len(client._dispatch_slots) == 3
    slots_sorted = sorted(client._dispatch_slots)
    for prev_s, next_s in zip(slots_sorted, slots_sorted[1:]):
        assert next_s - prev_s >= 0.08 - 1e-9, (
            f"Slot mới va chạm slot cũ: {slots_sorted} (wait_s={wait_s})"
        )

    # Kiểm tra cả cấu hình thực tế global_min_dispatch_interval_s = 1.5s khi có slot chờ phía trước
    cfg["gemini"]["global_min_dispatch_interval_s"] = 1.5
    cfg["gemini"]["backoff_max_s"] = 10.0
    client_prod = GeminiClient(cfg, keys)
    monkeypatch.setattr(_random, "uniform", lambda a, b: 0.20)
    client_prod._dispatch_slots = [base_now, base_now + 3.10]
    client_prod._reserve_gateway_wait(min_wait_s=0.0)
    prod_sorted = sorted(client_prod._dispatch_slots)
    for prev_s, next_s in zip(prod_sorted, prod_sorted[1:]):
        assert next_s - prev_s >= 1.5 - 1e-9, f"Slot production va chạm: {prod_sorted}"

    # Kiểm tra dãy jitter biến thiên ngẫu nhiên qua nhiều cụm burst liên tiếp
    rng = _random.Random(42)
    monkeypatch.setattr(_random, "uniform", rng.uniform)
    for _ in range(25):
        client._dispatch_slots.clear()
        for _slot_idx in range(6):
            client._reserve_gateway_wait(min_wait_s=0.0)
        ordered = sorted(client._dispatch_slots)
        for prev_s, next_s in zip(ordered, ordered[1:]):
            assert next_s - prev_s >= 0.08 - 1e-9


def test_bug_032_judge_step_model_routing_and_config_contract(tmp_path, monkeypatch):
    """BUG-032: judge có thể tách model riêng (model_by_step) sang Flash-Lite 500 RPD/key mà không ăn quota model chính."""
    from tomtat.llm import get_rpd_status

    cfg = load_config()
    cfg.validate(raise_on_error=True)
    g = cfg["gemini"]
    assert g["model_by_step"]["judge"] == "gemini-3.5-flash-lite"
    assert g["fallback_models_by_step"]["judge"] == ["gemini-3.1-flash-lite"]
    assert cfg["judge"]["only_if_checks_ok"] is True

    for m in [g["model_by_step"]["judge"], *g["fallback_models_by_step"]["judge"]]:
        assert m in g["rpd_limits"]
        assert m in g["rpm_limits"]
        assert m in g["min_interval_s"]

    monkeypatch.setattr("tomtat.llm._get_quota_cycle_date", lambda: "2026-01-01")
    keys = ["bad_key", "good_key"]
    monkeypatch.setattr("tomtat.config.get_api_keys", lambda root=None: keys)

    cfg["paths"] = {"work": str(tmp_path / "work")}
    cfg["gemini"]["min_interval_s"] = 0.0
    cfg["gemini"]["backoff_base_s"] = 0.0
    cfg["gemini"]["backoff_max_s"] = 0.0

    client = GeminiClient(cfg, keys)
    assert client.models_chain_for_step("judge") == ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
    assert client.models_chain_for_step("abstract")[0] == "gemini-4-hydrogen"

    # Trường hợp chỉ đặt model_by_step mà không đặt fallback_models_by_step -> tự cắt từ chuỗi chính, không nhảy ngược lên model đắt
    m_only, chains_only = GeminiClient._normalize_step_model_maps(
        {"model_by_step": {"judge": "gemini-3.5-flash-lite"}},
        ["gemini-4-hydrogen", "gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"],
    )
    assert m_only == {"judge": "gemini-3.5-flash-lite"}
    assert chains_only["judge"] == ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]

    client_map: dict[str, object] = {}
    called_models: list[tuple[str, str]] = []

    class Err401(Exception):
        status_code = 401

    class Err404(Exception):
        status_code = 404

    def fake_get(k: str):
        return client_map.setdefault(k, object())

    def fake_call_once(model_name, step, system, prompt, schema, client=None):
        k = next(key for key, obj in client_map.items() if obj is client)
        called_models.append((k, model_name))
        if k == "bad_key":
            raise Err401("API key not valid")
        if step == "judge" and model_name == "gemini-3.5-flash-lite":
            raise Err404("Temporary unavailable")
        return '{"issues": [], "summary": "OK", "relevance": 4.5, "coherence": 4.5, "consistency": 5.0, "fluency": 4.5}', {}, "completed"

    monkeypatch.setattr(client, "_get_or_create_client", fake_get)
    monkeypatch.setattr(client, "_call_once", fake_call_once)

    res = client.generate_json(step="judge", system="", prompt="p", schema={})
    assert res.model == "gemini-3.1-flash-lite"
    # judge tuyệt đối không gọi gemini-4-hydrogen / 3.8 / 3.6
    assert all(m in ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite") for _, m in called_models)
    # bad_key gặp 401 phải bị khóa trên toàn bộ model đã cấu hình
    for m in client._all_configured_models():
        assert ("bad_key", m) in client._exhausted

    # sync_config cập nhật nóng model_by_step & get_rpd_status hiển thị thêm model riêng nếu khác chuỗi chung
    cfg["gemini"]["model_by_step"]["judge"] = "gemini-3.1-flash-lite"
    cfg["gemini"]["fallback_models_by_step"]["judge"] = []
    client.sync_config(cfg)
    assert client.models_chain_for_step("judge") == ["gemini-3.1-flash-lite"]

    cfg_extra = load_config()
    cfg_extra["paths"] = {"work": str(tmp_path / "work2")}
    cfg_extra["gemini"]["model_by_step"] = {"judge": "gemini-3.6-flash"}
    cfg_extra["gemini"]["fallback_models"] = ["gemini-3.5-flash-lite"]
    rpd_models = [row["model"] for row in get_rpd_status(cfg_extra, api_keys=["k1"])]
    assert "gemini-3.6-flash" in rpd_models


def test_bug_032_judge_schema_reasoning_before_score_and_prompt_context():
    """BUG-032: JUDGE_SCHEMA phải đặt issues/summary trước 4 điểm số và judge.md hỗ trợ đủ rubric, query, đa văn bản."""
    from tomtat.prompts import JUDGE_SCHEMA, PromptRenderer

    prop_keys = list(JUDGE_SCHEMA["properties"].keys())
    req_keys = list(JUDGE_SCHEMA["required"])
    score_keys = ("relevance", "coherence", "consistency", "fluency")
    for list_keys in (prop_keys, req_keys):
        assert list_keys.index("issues") < min(list_keys.index(k) for k in score_keys)
        assert list_keys.index("summary") < min(list_keys.index(k) for k in score_keys)

    cfg = load_config()
    renderer = PromptRenderer(cfg.prompts_dir)
    rendered_multi = renderer.render(
        "judge.md",
        style="báo chí - công luận",
        style_guide=renderer.style_guide("báo chí - công luận"),
        docs=[{"n": 1}, {"n": 2}],
        query="Tác động của chính sách thuế mới là gì?",
        extract="Trích chọn mẫu.",
        abstract="Đoạn 1.\n\nĐoạn 2.",
        abstract_words=4,
        source_text="Văn bản gốc mẫu.",
    )
    assert "CÂU TRUY VẤN (QUERY)" in rendered_multi
    assert "Tác động của chính sách thuế mới là gì?" in rendered_multi
    assert "ĐA VĂN BẢN (2 văn bản độc lập)" in rendered_multi
    assert "KHÔNG được trừ điểm `coherence`" in rendered_multi
    assert "THANG ĐIỂM NEO RÕ" in rendered_multi

    rendered_single = renderer.render(
        "judge.md",
        style="khoa học",
        style_guide=renderer.style_guide("khoa học"),
        docs=[{"n": 1}],
        query="",
        extract="Trích chọn.",
        abstract="Biên soạn.",
        abstract_words=2,
        source_text="Nguồn.",
    )
    assert "Đơn văn bản" in rendered_single
    assert "CÂU TRUY VẤN (QUERY)" not in rendered_single
    assert "QUY TẮC RIÊNG CHO ĐA VĂN BẢN" not in rendered_single


def test_bug_032_step_judge_passes_query_docs_and_respects_checks_gate(tmp_path):
    """BUG-032: step_judge truyền query/docs, lưu model_used, làm sạch cờ cũ và process_file tôn trọng only_if_checks_ok."""
    from tomtat.llm.base import BaseClient, LLMResult
    from tomtat.pipeline import process_file, step_judge
    from tomtat.prompts import PromptRenderer
    from tomtat.state import FileWorkspace

    cfg = load_config()
    cfg["paths"] = {
        "input": str(tmp_path / "input"),
        "output": str(tmp_path / "output"),
        "work": str(tmp_path / "work"),
        "prompts": str(cfg.prompts_dir),
        "report": str(tmp_path / "output" / "baocao.xlsx"),
    }
    cfg["judge"]["enabled"] = True
    cfg["judge"]["only_if_checks_ok"] = True
    cfg["judge"]["rewrite_once_if_below"] = False

    sample = cfg.root / "samples" / "M54629.docx"
    inp = tmp_path / "input" / "M54629.docx"
    inp.parent.mkdir(parents=True, exist_ok=True)
    inp.write_bytes(sample.read_bytes())

    class SpyJudgeClient(BaseClient):
        def __init__(self, fail_abstract: bool = False):
            self.fail_abstract = fail_abstract
            self.calls: list[str] = []
            self.judge_prompts: list[str] = []
            self.next_judge_scores = {
                "issues": ["[Tính nhất quán] Suy diễn nhẹ -> Bám sát gốc"],
                "summary": "Nhất quán còn vướng 1 chi tiết.",
                "relevance": 6.0,      # cố tình vượt biên để kiểm tra clamp 5.0
                "coherence": 4.0,
                "consistency": 3.0,
                "fluency": 4.0,
            }

        def generate_json(self, step, system, prompt, schema, *, log_dir=None, attempt=0, context=None):
            self.calls.append(step)
            if step == "label":
                return LLMResult(
                    data={"label": "báo chí - công luận", "per_doc": [], "confidence": "cao", "reason": "ok"},
                    text="{}",
                    model="gemini-4-hydrogen",
                )
            if step == "extract":
                data = context["data"]
                sels = [{"doc": d["n"], "ids": [u["id"] for u in d["units"][:2]]} for d in data["docs"]]
                return LLMResult(data={"selections": sels, "note": "ok"}, text="{}", model="gemini-4-hydrogen")
            if step == "abstract":
                if self.fail_abstract:
                    # Viết dài quá tỷ lệ 70% để checks.ok == False
                    long_txt = " ".join(["từ"] * 500)
                    return LLMResult(data={"abstract": long_txt, "note": "too long"}, text="{}", model="gemini-4-hydrogen")
                return LLMResult(data={"abstract": "Tóm tắt ngắn gọn.", "note": "ok"}, text="{}", model="gemini-4-hydrogen")
            if step == "judge":
                self.judge_prompts.append(prompt)
                return LLMResult(
                    data=dict(self.next_judge_scores),
                    text="{}",
                    model="gemini-3.5-flash-lite",
                )
            raise AssertionError(step)

    # 1. Nếu bản biên soạn không đạt checks.ok -> process_file KHÔNG gọi judge lãng phí RPD
    bad_client = SpyJudgeClient(fail_abstract=True)
    out_bad = process_file(inp, cfg, bad_client, force=True, restart=True)
    assert out_bad["state"]["checks"]["ok"] is False
    assert out_bad["state"]["judge"] is None
    assert "judge" not in bad_client.calls

    # 2. Nhưng bấm chạy riêng AI: Chấm G-Eval (only_step="judge") vẫn phải chạy được và lưu model_used
    out_manual = process_file(inp, cfg, bad_client, only_step="judge")
    st_j = out_manual["state"]["judge"]
    assert st_j is not None
    assert st_j["model_used"] == "gemini-3.5-flash-lite"
    assert st_j["relevance"] == 5.0  # clamped từ 6.0 -> 5.0
    assert st_j["average"] == 4.0
    assert any(f.startswith("Điểm G-Eval tự chấm (4.0)") for f in out_manual["state"]["flags"])

    # 3. Khi chạy lại step_judge đạt >= 4.25 -> cờ điểm thấp cũ (4.0) phải bị dọn sạch, query & đa văn bản được truyền vào prompt
    ws = FileWorkspace(cfg.work_dir, inp)
    units_data = ws.load_units()
    units_data["query"] = "Câu hỏi kiểm thử đặc thù 2026"
    st_cur = ws.load_state()
    st_cur["flags"].append("Bỏ qua bước tự chấm G-Eval do lỗi trước đó.")
    bad_client.next_judge_scores = {
        "issues": [],
        "summary": "Tốt toàn diện.",
        "relevance": 4.5,
        "coherence": 4.5,
        "consistency": 5.0,
        "fluency": 4.5,
    }
    step_judge(ws, units_data, bad_client, PromptRenderer(cfg.prompts_dir), cfg, st_cur)
    assert st_cur["judge"]["average"] == 4.62
    assert st_cur["judge"]["model_used"] == "gemini-3.5-flash-lite"
    assert not any("G-Eval" in f for f in st_cur["flags"])
    assert "Câu hỏi kiểm thử đặc thù 2026" in bad_client.judge_prompts[-1]

    # 4. Kiểm tra hợp đồng hiển thị model_badge_judge trên giao diện
    html = (PROJECT_ROOT / "src" / "tomtat" / "web" / "templates" / "index.html").read_text(encoding="utf-8")
    editor_js = (PROJECT_ROOT / "src" / "tomtat" / "web" / "static" / "modules" / "editor.js").read_text(encoding="utf-8")
    assert 'id="model-badge-judge"' in html
    assert 'setStepBadge("model-badge-judge"' in editor_js
    assert "G-Eval: ${mJudge}" in editor_js


# ---------------------------------------------------------------------------
# BUG-033: day_cap_nhat.bat đẩy GitHub 1-click an toàn, đồng bộ & chặn lộ bí mật
# ---------------------------------------------------------------------------

def test_bug_033_push_helper_staged_safety_and_commit_message(tmp_path):
    """BUG-033: push_helper.py phải chặn mọi file bí mật/dữ liệu cá nhân đang stage
    và tự động tạo commit message ASCII rõ ràng từ thay đổi thực tế."""
    import importlib.util
    import json
    import subprocess

    helper_path = PROJECT_ROOT / ".agents" / "push_helper.py"
    spec = importlib.util.spec_from_file_location("push_helper", helper_path)
    assert spec is not None and spec.loader is not None
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)

    # 1. Kiểm tra bộ lọc đường dẫn nhạy cảm
    for forbidden in (
        "api_keys.txt",
        ".env",
        "config.local.yaml",
        "work/quota_exhausted.json",
        "work/S89769/state.json",
        "input/S89769.docx",
        "output/S89769.docx",
        "TomTatBienSoan_ChiaSe.zip",
        "docs/~$HUONG_DAN.docx",
    ):
        assert helper.is_forbidden_staged_path(forbidden) is not None, f"Phải chặn {forbidden}"

    for allowed in (
        "src/tomtat/pipeline.py",
        "config.yaml",
        "day_cap_nhat.bat",
        ".agents/push_helper.py",
        "work/learned_memory.json",
        "work/.gitkeep",
        "input/.gitkeep",
        "samples/M54629.docx",
        "docs/KNOWN_BUGS.md",
    ):
        assert helper.is_forbidden_staged_path(allowed) is None, f"Không được chặn nhầm {allowed}"

    # 2. Kiểm tra sinh commit message trên repo git thật trong tmp_path
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(repo), check=True)

    (repo / "docs").mkdir()
    (repo / "work").mkdir()
    (repo / "docs" / "KNOWN_BUGS.md").write_text("### BUG-001: Cu\n", encoding="utf-8")
    (repo / "work" / "learned_memory.json").write_text(json.dumps({"total_approved_learned": 97}), encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), check=True, capture_output=True)

    # Trường hợp A: chỉ cập nhật learned_memory.json
    (repo / "work" / "learned_memory.json").write_text(json.dumps({"total_approved_learned": 98}), encoding="utf-8")
    subprocess.run(["git", "add", "work/learned_memory.json"], cwd=str(repo), check=True)
    ok_mem, bad_mem = helper.check_staged(repo)
    assert ok_mem is True and bad_mem == []
    msg_mem = helper.build_commit_message(repo)
    assert "bo nho hoc tap" in msg_mem
    assert "98 bai" in msg_mem

    # Trường hợp B: thêm mục BUG mới vào docs/KNOWN_BUGS.md
    (repo / "docs" / "KNOWN_BUGS.md").write_text(
        "### BUG-001: Cu\n### BUG-033: Nang cap day cap nhat GitHub\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "docs/KNOWN_BUGS.md"], cwd=str(repo), check=True)
    msg_bug = helper.build_commit_message(repo)
    assert "BUG-033" in msg_bug
    assert "learned_memory" in msg_bug
    assert msg_bug.isascii()

    # Trường hợp C: lỡ stage file nhạy cảm -> check_staged phải chặn
    (repo / "api_keys.txt").write_text("AIzaSySECRET", encoding="utf-8")
    subprocess.run(["git", "add", "-f", "api_keys.txt"], cwd=str(repo), check=True)
    ok_bad, bad_list = helper.check_staged(repo)
    assert ok_bad is False
    assert any(p == "api_keys.txt" for p, _ in bad_list)


def test_bug_033_day_cap_nhat_bat_contract_and_e2e(tmp_path, monkeypatch):
    """BUG-033: day_cap_nhat.bat phải giữ đúng hợp đồng tĩnh (CRLF, thuần ASCII, không *.txt,
    chạy verify_guard/pytest, đồng bộ nhánh main) và đẩy thành công kể cả khi remote có commit mới."""
    import os
    import shutil
    import subprocess

    bat_path = PROJECT_ROOT / "day_cap_nhat.bat"
    raw = bat_path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert raw.count(b"\n") == raw.count(b"\r\n")
    assert all(b < 128 for b in raw), "day_cap_nhat.bat phải thuần ASCII"

    bat_text = raw.decode("ascii")
    for required in (
        "verify_guard.py",
        "pytest.exe",
        "pyproject.toml",
        "push_helper.py",
        "work\\learned_memory.json",
        "git fetch origin main",
        "git pull --rebase",
        "--no-pause",
    ):
        assert required in bat_text
    assert "*.txt" not in bat_text

    # Mô phỏng chạy thật bằng cmd.exe với bare remote cục bộ
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)

    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", str(bare), str(seed)], check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-B", "main"], cwd=str(seed), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Seed"], cwd=str(seed), check=True)
    subprocess.run(["git", "config", "user.email", "seed@example.com"], cwd=str(seed), check=True)
    (seed / "src").mkdir()
    (seed / "src" / "app.py").write_text("v = 1\n", encoding="utf-8")
    (seed / "work").mkdir()
    (seed / "work" / "learned_memory.json").write_text('{"total_approved_learned": 1}\n', encoding="utf-8")
    (seed / ".github_repo_url").write_text(f"{bare}\n", encoding="utf-8")
    (seed / ".gitignore").write_text(".env\napi_keys.txt\nconfig.local.yaml\nwork/\ninput/*.docx\n", encoding="utf-8")
    subprocess.run(["git", "add", "-f", "work/learned_memory.json"], cwd=str(seed), check=True)
    subprocess.run(["git", "add", "."], cwd=str(seed), check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=str(seed), check=True, capture_output=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=str(seed), check=True, capture_output=True)

    # Clone bản làm việc từ bare remote
    local_repo = tmp_path / "local_repo"
    subprocess.run(["git", "clone", "-b", "main", str(bare), str(local_repo)], check=True, capture_output=True)
    shutil.copy2(bat_path, local_repo / "day_cap_nhat.bat")
    (local_repo / ".agents").mkdir(exist_ok=True)
    shutil.copy2(PROJECT_ROOT / ".agents" / "push_helper.py", local_repo / ".agents" / "push_helper.py")

    # Tạo commit mới trên bare remote (giả lập máy khác vừa push trước)
    (seed / "README.md").write_text("# Remote newer commit\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=str(seed), check=True)
    subprocess.run(["git", "commit", "-m", "remote commit"], cwd=str(seed), check=True, capture_output=True)
    subprocess.run(["git", "push", "origin", "main"], cwd=str(seed), check=True, capture_output=True)

    # Ở máy local: sửa code + learned_memory + đặt file bí mật (tuyệt đối không được đẩy)
    (local_repo / "src" / "app.py").write_text("v = 2\n", encoding="utf-8")
    (local_repo / "work" / "learned_memory.json").write_text('{"total_approved_learned": 2}\n', encoding="utf-8")
    (local_repo / "api_keys.txt").write_text("AIzaSyDO_NOT_PUSH\n", encoding="utf-8")
    (local_repo / ".env").write_text("GEMINI_API_KEY=SECRET\n", encoding="utf-8")
    (local_repo / "config.local.yaml").write_text("workers: 1\n", encoding="utf-8")
    (local_repo / "input").mkdir(exist_ok=True)
    (local_repo / "input" / "doc.docx").write_bytes(b"secret")

    url_before = (local_repo / ".github_repo_url").read_bytes()
    env = os.environ.copy()
    env["TOMTAT_PUSH_REPO"] = str(bare)
    res = subprocess.run(
        ["cmd.exe", "/c", "day_cap_nhat.bat", "--no-pause"],
        cwd=str(local_repo),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert res.returncode == 0, f"STDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
    assert "[THANH CONG]" in res.stdout
    assert (local_repo / ".github_repo_url").read_bytes() == url_before

    # Kiểm tra nội dung đã lên bare remote
    ls_remote = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "refs/heads/main"],
        cwd=str(bare),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert "src/app.py" in ls_remote
    assert "work/learned_memory.json" in ls_remote
    assert "README.md" in ls_remote
    for secret in ("api_keys.txt", ".env", "config.local.yaml", "input/doc.docx"):
        assert secret not in ls_remote


# ---------------------------------------------------------------------------
# BUG-034: Loại bỏ nút thừa "Ghép lại từ các câu đã tick" (#btn-rebuild-extract)
# ---------------------------------------------------------------------------

def test_bug_034_rebuild_extract_button_removed_cleanly(tmp_path, monkeypatch):
    """BUG-034: Bỏ nút #btn-rebuild-extract khỏi HTML và listener khỏi app.js,
    nhưng vẫn giữ nguyên tự động ghép qua onToggleUnit / Chọn hết / Bỏ hết và API /assemble."""
    from tomtat.llm import MockClient
    from tomtat.pipeline import process_file
    from tomtat.web import create_app

    html = (PROJECT_ROOT / "src" / "tomtat" / "web" / "templates" / "index.html").read_text(encoding="utf-8")
    assert "btn-rebuild-extract" not in html
    assert "Ghép lại từ các câu đã tick" not in html
    assert 'id="txt-extract"' in html
    assert 'id="docs-container"' in html
    assert 'id="verbatim-bar"' in html

    static_dir = PROJECT_ROOT / "src" / "tomtat" / "web" / "static"
    for js_file in static_dir.rglob("*.js"):
        assert "btn-rebuild-extract" not in js_file.read_text(encoding="utf-8"), (
            f"{js_file.name} vẫn còn tham chiếu tới #btn-rebuild-extract"
        )

    app_js = (static_dir / "app.js").read_text(encoding="utf-8")
    assert "assembleFromSelections" not in app_js

    editor_js = (static_dir / "modules" / "editor.js").read_text(encoding="utf-8")
    assert "export async function onToggleUnit" in editor_js
    assert "await assembleFromSelections()" in editor_js
    assert "export async function assembleFromSelections" in editor_js

    cfg, inp = _setup_version_test_env(tmp_path, monkeypatch)
    process_file(inp, cfg, MockClient(cfg), force=True)
    app = create_app(cfg, mock=True)
    tc = app.test_client()

    r_asm = tc.post(
        "/api/file/M54629/assemble",
        json={"selections": {"1": [1, 2]}, "style": "", "abstract": ""},
    )
    assert r_asm.status_code == 200
    d_asm = r_asm.get_json()
    assert d_asm["extract"].strip() != ""
    assert d_asm["selections"]["1"] == [1, 2]


# ---------------------------------------------------------------------------
# BUG-035: Theo dõi trạng thái còn/hết RPD từng model theo từng key & fix lệch khóa cục bộ
# ---------------------------------------------------------------------------

def test_bug_035_local_rpd_counter_never_exhausts_key_before_google_429_and_persists_per_key_counts(tmp_path, monkeypatch):
    """BUG-035: Bộ đếm cục bộ (kể cả khi gặp nhiều 503) tuyệt đối không tự khóa (key, model) vào _exhausted
    trước khi Google thực sự trả 429; lỗi ConnectError/400/402 không tính RPD; và số liệu từng key bền vững qua restart."""
    from tomtat.llm.base import _error_consumes_rpd, get_key_rpd_details

    monkeypatch.setattr("tomtat.llm._get_quota_cycle_date", lambda: "2026-01-01")
    monkeypatch.setattr("tomtat.llm.gemini._COOLDOWN_S", 0.0)
    monkeypatch.setattr("tomtat.llm.base.get_model_rpm_per_key", lambda m, c=None: 10_000)
    monkeypatch.setattr("tomtat.llm.gemini.get_model_rpm_per_key", lambda m, c=None: 10_000)

    assert _error_consumes_rpd("APIConnectionError: Connection error.") is False
    assert _error_consumes_rpd("BadRequestError: invalid argument", 400) is False
    assert _error_consumes_rpd("PaymentRequiredError", 402) is False
    assert _error_consumes_rpd("InternalServerError: 503 high demand", 503) is True

    keys = ["AIzaTestKey_Alpha_0001"]
    monkeypatch.setattr("tomtat.config.get_api_keys", lambda root=None: keys)

    cfg = load_config()
    cfg["paths"] = {"work": str(tmp_path / "work")}
    cfg["gemini"]["rpd_limits"] = {m: 3 for m in [cfg["gemini"]["model"]] + list(cfg["gemini"].get("fallback_models") or [])}
    cfg["gemini"]["min_interval_s"] = 0.0
    cfg["gemini"]["rpm_limits"] = {}
    cfg["gemini"]["backoff_base_s"] = 0.0
    cfg["gemini"]["backoff_max_s"] = 0.0
    cfg["gemini"]["max_rounds_per_model"] = 10
    cfg["gemini"]["max_transient_per_model"] = 0

    client = GeminiClient(cfg, keys)
    outcomes = ["503", "503", "503", "conn", "ok"]

    class FakeErr(Exception):
        def __init__(self, code, msg):
            super().__init__(msg)
            self.status_code = code

    def fake_call_once(model_name, step, system, prompt, schema, client=None):
        act = outcomes.pop(0) if outcomes else "ok"
        if act == "503":
            raise FakeErr(503, "high demand service_unavailable")
        if act == "conn":
            raise ConnectionError("broken pipe")
        return '{"ok": true}', {}, "completed"

    monkeypatch.setattr(client, "_get_or_create_client", lambda k: object())
    monkeypatch.setattr(client, "_call_once", fake_call_once)

    log_dir = tmp_path / "work" / "S01" / "logs"
    res = client.generate_json(step="label", system="sys", prompt="p", schema={}, log_dir=log_dir, attempt=1)
    assert res.model == "gemini-4-hydrogen"
    # Đã vượt mốc cấu hình 3 requests (3 lỗi 503 + 1 OK = 4 RPD ước tính), nhưng Google chưa hề trả 429 -> không bị khóa!
    assert (keys[0], "gemini-4-hydrogen") not in client._exhausted
    assert client._key_rpd_counts[(keys[0], "gemini-4-hydrogen")] == 4
    assert client._key_ok_counts[(keys[0], "gemini-4-hydrogen")] == 1
    assert client._key_transient_counts[(keys[0], "gemini-4-hydrogen")] == 3

    # Khởi tạo client mới (giả lập khởi động lại server): giữ nguyên số liệu từng key và vẫn gọi tiếp được key này
    client2 = GeminiClient(cfg, keys)
    assert (keys[0], "gemini-4-hydrogen") not in client2._exhausted
    assert client2._key_rpd_counts[(keys[0], "gemini-4-hydrogen")] == 4
    assert client2._key_ok_counts[(keys[0], "gemini-4-hydrogen")] == 1

    details = get_key_rpd_details(cfg, api_keys=keys, live_client=client2)
    cell = details["keys"][0]["models"]["gemini-4-hydrogen"]
    assert cell["exhausted"] is False
    assert cell["ok_used"] == 1
    assert cell["err_503_used"] == 3
    assert cell["used"] == 4


def test_bug_035_explicit_rpm_429_uses_full_window_cooldown_and_records_exhaust_reason(tmp_path, monkeypatch):
    """BUG-035: Lỗi 429 RPM/TPM phải hạ nhiệt >= 65s (qua trọn cửa sổ 60s) và ghi rõ lý do khóa trong exhausted_meta."""
    monkeypatch.setattr("tomtat.llm._get_quota_cycle_date", lambda: "2026-01-01")
    monkeypatch.setattr("tomtat.llm.gemini._COOLDOWN_S", 30.0)
    monkeypatch.setattr("tomtat.llm.base.get_model_rpm_per_key", lambda m, c=None: 10_000)
    monkeypatch.setattr("tomtat.llm.gemini.get_model_rpm_per_key", lambda m, c=None: 10_000)

    keys = ["AIzaTestKey_RPM_0001", "AIzaTestKey_RPM_0002"]
    monkeypatch.setattr("tomtat.config.get_api_keys", lambda root=None: keys)

    cfg = load_config()
    cfg["paths"] = {"work": str(tmp_path / "work")}
    cfg["gemini"]["min_interval_s"] = 0.0
    cfg["gemini"]["rpm_limits"] = {}
    cfg["gemini"]["backoff_base_s"] = 0.0
    cfg["gemini"]["backoff_max_s"] = 0.0

    client = GeminiClient(cfg, keys)
    client_map = {}

    class Err429(Exception):
        status_code = 429

    def fake_get(k):
        return client_map.setdefault(k, object())

    mode = {"k1": "rpm"}

    def fake_call_once(model_name, step, system, prompt, schema, client=None):
        k = next(key for key, obj in client_map.items() if obj is client)
        if k == keys[0] and mode["k1"] == "rpm":
            raise Err429("Quota exceeded for quota metric 'GenerateContentInputTokensPerModelPerMinute' (TPM)")
        if k == keys[0] and mode["k1"] == "daily":
            raise Err429("Rate limit exceeded for model (limit: 20 requests per day on Free Tier).")
        return '{"ok": true}', {}, "completed"

    monkeypatch.setattr(client, "_get_or_create_client", fake_get)
    monkeypatch.setattr(client, "_call_once", fake_call_once)

    import time as _t
    t_before = _t.time()
    client.generate_json(step="label", system="sys", prompt="p1", schema={})
    cd_until = client._key_cooldown.get(keys[0], 0.0)
    assert cd_until - t_before >= 60.0  # đủ qua cửa sổ 60s của Google RPM/TPM

    # Khi 1 cặp (key, model) gặp 429 daily đủ 3 lần -> ghi rõ lý do trong _exhausted_meta và giữ qua restart
    monkeypatch.setattr("tomtat.llm.gemini._COOLDOWN_S", 0.0)
    client._key_cooldown.clear()
    mode["k1"] = "daily"
    for _ in range(2):
        client._model_cursor["gemini-4-hydrogen"] = 0
        client.generate_json(step="label", system="sys", prompt="p2", schema={})
    assert (keys[0], "gemini-4-hydrogen") in client._exhausted
    meta = client._exhausted_meta.get((keys[0], "gemini-4-hydrogen"))
    assert meta and meta["reason"] == "429_daily"
    assert "requests per day" in meta["error"].lower()

    client_reloaded = GeminiClient(cfg, keys)
    assert client_reloaded._exhausted_meta[(keys[0], "gemini-4-hydrogen")]["reason"] == "429_daily"


def test_bug_035_per_key_rpd_details_api_unlock_and_probe(tmp_path, monkeypatch):
    """BUG-035: API /api/rpd/keys, /api/rpd/probe và /api/rpd/unlock hiển thị chi tiết từng key/model (không lộ key thật),
    đồng bộ cả RAM (_CACHED_CLIENT) lẫn quota_exhausted.json và tự mở khóa khi probe xác nhận Google còn RPD."""
    from types import SimpleNamespace
    import tomtat.services.batch_service as bs
    from tomtat.web import create_app

    monkeypatch.setattr("tomtat.llm._get_quota_cycle_date", lambda: "2026-01-01")
    keys = ["AIzaSySecretKeyOne_AAAA", "AIzaSySecretKeyTwo_BBBB"]
    monkeypatch.setattr("tomtat.config.get_api_keys", lambda root=None: keys)

    cfg = load_config()
    cfg["paths"] = {
        "input": str(tmp_path / "input"),
        "output": str(tmp_path / "output"),
        "work": str(tmp_path / "work"),
        "prompts": str(cfg.prompts_dir),
        "report": str(tmp_path / "output" / "baocao.xlsx"),
    }

    old_cached = bs._CACHED_CLIENT
    old_keys = list(bs._CACHED_KEYS)
    try:
        bs._CACHED_CLIENT = None
        bs._CACHED_KEYS = []

        live = bs.make_client(cfg, force_mock=False)
        live._mark_exhausted(keys[0], "gemini-4-hydrogen", reason="429_daily", error="429 per day")
        live._record_request(keys[1], "gemini-4-hydrogen")

        app = create_app(cfg, mock=False)
        tc = app.test_client()

        r_keys = tc.get("/api/rpd/keys")
        assert r_keys.status_code == 200
        raw_json = r_keys.get_data(as_text=True)
        # Tuyệt đối không lộ API key thô ra API web
        for secret in keys:
            assert secret not in raw_json

        d = r_keys.get_json()
        assert d["key_count"] == 2
        row0, row1 = d["keys"]
        assert row0["key_label"] == "Key #1 (...AAAA)"
        assert row0["models"]["gemini-4-hydrogen"]["exhausted"] is True
        assert row0["models"]["gemini-4-hydrogen"]["reason_code"] == "429_daily"
        assert row1["models"]["gemini-4-hydrogen"]["exhausted"] is False
        assert row1["models"]["gemini-4-hydrogen"]["ok_used"] == 1

        # 1. Probe gặp 429 thật -> giữ nguyên trạng thái khóa và trả lỗi nguyên văn
        class Err429(Exception):
            status_code = 429

        probe_mode = {"mode": "429"}

        class FakeInteractions:
            def create(self, **kwargs):
                if probe_mode["mode"] == "429":
                    raise Err429("Rate limit exceeded (limit: 20 requests per day)")
                return SimpleNamespace(output_text="OK", status="completed")

        monkeypatch.setattr(live, "_get_or_create_client", lambda k: SimpleNamespace(interactions=FakeInteractions()))
        r_p1 = tc.post("/api/rpd/probe", json={"key_id": row0["key_id"], "model": "gemini-4-hydrogen"})
        assert r_p1.status_code == 200
        p1 = r_p1.get_json()
        assert p1["outcome"] == "exhausted_rpd"
        assert p1["unlocked"] is False
        assert (keys[0], "gemini-4-hydrogen") in live._exhausted

        # 2. Probe trả 200 OK -> xác nhận thực tế vẫn còn RPD, tự mở khóa cả RAM và đĩa
        probe_mode["mode"] = "ok"
        r_p2 = tc.post("/api/rpd/probe", json={"key_id": row0["key_id"], "model": "gemini-4-hydrogen"})
        assert r_p2.status_code == 200
        p2 = r_p2.get_json()
        assert p2["outcome"] == "available"
        assert p2["unlocked"] is True
        assert (keys[0], "gemini-4-hydrogen") not in live._exhausted
        assert p2["details"]["keys"][0]["models"]["gemini-4-hydrogen"]["ok_used"] == 1

        # 3. Đánh dấu khóa lại rồi mở khóa qua /api/rpd/unlock
        live._mark_exhausted(keys[0], "gemini-3.8-flash", reason="429_rpm_3x", error="429 rpm")
        assert (keys[0], "gemini-3.8-flash") in live._exhausted
        r_unl = tc.post("/api/rpd/unlock", json={"key_id": row0["key_id"], "model": "gemini-3.8-flash"})
        assert r_unl.status_code == 200
        assert (keys[0], "gemini-3.8-flash") not in live._exhausted

        # 4. Trong chế độ --mock: /api/rpd/probe từ chối rõ ràng
        app_mock = create_app(cfg, mock=True)
        r_mock_p = app_mock.test_client().post("/api/rpd/probe", json={"key_id": row0["key_id"], "model": "gemini-4-hydrogen"})
        assert r_mock_p.status_code == 400
    finally:
        bs._CACHED_CLIENT = old_cached
        bs._CACHED_KEYS = old_keys


def test_bug_035_frontend_rpd_modal_and_cli_contract(tmp_path, monkeypatch, capsys):
    """BUG-035: Hợp đồng UI modal chi tiết RPD theo từng key/model và lệnh CLI `tomtat rpd`."""
    from tomtat import cli

    html = (PROJECT_ROOT / "src" / "tomtat" / "web" / "templates" / "index.html").read_text(encoding="utf-8")
    assert 'id="btn-rpd-details"' in html
    assert 'id="rpd-models-list"' in html

    static_dir = PROJECT_ROOT / "src" / "tomtat" / "web" / "static"
    modals_js = (static_dir / "modules" / "modals.js").read_text(encoding="utf-8")
    assert "export async function openRpdModal" in modals_js
    assert "/api/rpd/keys" in modals_js
    assert "/api/rpd/unlock" in modals_js
    assert "/api/rpd/probe" in modals_js

    app_js = (static_dir / "app.js").read_text(encoding="utf-8")
    assert "openRpdModal" in app_js
    assert "btn-rpd-details" in app_js

    monkeypatch.setattr("tomtat.llm._get_quota_cycle_date", lambda: "2026-01-01")
    keys = ["AIzaCliTestKey_1111"]
    monkeypatch.setattr("tomtat.config.get_api_keys", lambda root=None: keys)
    cfg = load_config()
    cfg["paths"] = {"work": str(tmp_path / "work")}
    monkeypatch.setattr("tomtat.config.load_config", lambda *a, **kw: cfg)

    rc = cli.main(["rpd"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Key #1 (...1111)" in out
    assert "gemini-4-hydrogen" in out


# ═══════════════════════════════════════════════════════════════════════════════
# BUG-036: Lỗi 504 deadline_exceeded ở mốc 600s, thiếu background execution,
#          thiếu thinking_level và circuit breaker timeout dùng chung giữa các luồng
# ═══════════════════════════════════════════════════════════════════════════════

def test_bug_036_config_thinking_level_and_deadline_helpers(monkeypatch, tmp_path):
    import yaml
    from pathlib import Path
    from tomtat.llm.base import _is_server_deadline_exceeded, _is_timeout_error
    from tomtat.llm.gemini import GeminiClient

    monkeypatch.setattr("tomtat.llm._get_quota_cycle_date", lambda: "2026-01-01")
    cfg_path = Path(__file__).resolve().parents[1] / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    cfg["paths"] = {"work": str(tmp_path / "work")}
    g = cfg["gemini"]

    assert g["background_execution"] == "auto"
    assert int(g["background_min_timeout_s"]) == 540
    assert float(g["background_poll_interval_s"]) > 0
    assert int(g["model_timeout_outage_threshold"]) == 2
    assert float(g["model_timeout_outage_cooldown_s"]) >= 60
    assert g["thinking_level"] == {
        "label": "medium",
        "extract": "medium",
        "abstract": "medium",
        "judge": "low",
    }
    assert g["thinking_level_by_model"] == {
        "gemini-3.5-flash-lite": {"abstract": "low"},
        "gemini-3.1-flash-lite": {"abstract": "low"},
    }

    exc_504 = RuntimeError(
        "InternalServerError: Error code: 504 - {'error': {'message': "
        "'Deadline expired before operation could complete.', 'code': 'deadline_exceeded'}}"
    )
    assert _is_timeout_error(exc_504) is True
    assert _is_server_deadline_exceeded(exc_504) is True
    assert _is_server_deadline_exceeded(RuntimeError("x"), status_code=504) is True
    exc_read = TimeoutError("read timed out")
    assert _is_timeout_error(exc_read) is True
    assert _is_server_deadline_exceeded(exc_read) is False

    client = GeminiClient(cfg, ["AIzaFakeKeyForTest036"])
    # Chất lượng đồng đều: cả 4-hydrogen, 3.8-flash và 3.6-flash đều dùng medium ở label/extract/abstract
    for model_name in ("gemini-4-hydrogen", "gemini-3.8-flash", "gemini-3.6-flash"):
        assert client._gen_config("label", model_name)["thinking_level"] == "medium"
        assert client._gen_config("extract", model_name)["thinking_level"] == "medium"
        assert client._gen_config("abstract", model_name)["thinking_level"] == "medium"
        assert client._gen_config("judge", model_name)["thinking_level"] == "low"
    # Flash-Lite nâng riêng abstract lên low khi fallback sâu, còn judge/label/extract vẫn giữ minimal gốc nếu không override
    assert client._gen_config("abstract", "gemini-3.5-flash-lite")["thinking_level"] == "low"
    assert client._gen_config("abstract", "gemini-3.1-flash-lite")["thinking_level"] == "low"
    assert "thinking_level" not in client._gen_config("judge", "gemini-3.5-flash-lite")
    assert "thinking_level" not in client._gen_config("extract", "gemini-3.1-flash-lite")
    cfg["gemini"]["thinking_level_by_model"]["gemini-3.5-flash-lite"]["judge"] = "low"
    assert client._gen_config("judge", "gemini-3.5-flash-lite")["thinking_level"] == "low"


def test_bug_036_background_execution_polling_cancel_and_sync_fallback(monkeypatch, tmp_path):
    import threading
    import yaml
    from pathlib import Path
    from types import SimpleNamespace
    from tomtat.config import load_config
    from tomtat.llm.base import LLMCancelledError, bind_cancel_event
    from tomtat.llm.gemini import GeminiClient
    from tomtat.services.file_service import FileService

    monkeypatch.setattr("tomtat.llm._get_quota_cycle_date", lambda: "2026-01-01")
    cfg_app = load_config()
    cfg_app["paths"] = {
        "input": str(tmp_path / "input"),
        "output": str(tmp_path / "output"),
        "work": str(tmp_path / "work"),
        "prompts": str(cfg_app.prompts_dir),
        "report": str(tmp_path / "output" / "baocao.xlsx"),
    }
    cfg = yaml.safe_load((Path(__file__).resolve().parents[1] / "config.yaml").read_text(encoding="utf-8"))
    cfg["paths"] = dict(cfg_app["paths"])
    cfg["gemini"]["models_chain"] = ["gemini-4-hydrogen"]
    cfg["gemini"]["min_interval_s"] = 0.0
    cfg["gemini"]["rpm_limits"] = {}
    cfg["gemini"]["backoff_min_s"] = 0.0
    cfg["gemini"]["backoff_max_s"] = 0.0
    cfg["gemini"]["retry_delay_base_s"] = 0.0
    cfg["gemini"]["background_poll_interval_s"] = 0.01

    client = GeminiClient(cfg, ["AIzaFakeKeyForTest036B"])

    # Mock tối giản chỉ có create() -> không bật background để giữ tương thích test cũ
    mock_sync_only = SimpleNamespace(interactions=SimpleNamespace(create=lambda **kw: None))
    assert client._should_use_background(600.0, mock_sync_only) is False
    assert client._should_use_background(None, mock_sync_only) is False

    # Scenario 1: background=True + store=True + poll + transient GET retry + delete cleanup + FileService/UI log
    calls = []
    deleted = []
    get_attempt = {"n": 0}

    class FakeInteractionsPolling:
        def create(self, **kw):
            calls.append(dict(kw))
            return SimpleNamespace(id="inter-036-1", status="in_progress", output_text="")

        def get(self, *, id, timeout=None):
            get_attempt["n"] += 1
            if get_attempt["n"] == 1:
                return SimpleNamespace(id=id, status="in_progress", output_text="")
            if get_attempt["n"] == 2:
                raise ConnectionResetError("Connection reset by peer")
            return SimpleNamespace(
                id=id,
                status="completed",
                output_text='{"ok": true, "mode": "bg"}',
                usage=SimpleNamespace(total_input_tokens=10, total_output_tokens=5, total_thought_tokens=2),
            )

        def delete(self, *, id, timeout=None):
            deleted.append(id)

        def cancel(self, *, id, timeout=None):
            pass

    fake_sdk = SimpleNamespace(interactions=FakeInteractionsPolling())
    monkeypatch.setattr(client, "_get_or_create_client", lambda key: fake_sdk)
    fs = FileService(cfg_app)
    ws = fs.get_workspace("doc1")
    res = client.generate_json(
        step="abstract",
        system="sys",
        prompt="prompt",
        schema={"type": "object", "properties": {"ok": {"type": "boolean"}, "mode": {"type": "string"}}},
        log_dir=ws.logs_dir,
    )
    assert res.data == {"ok": True, "mode": "bg"}
    assert calls[0].get("background") is True
    assert calls[0].get("store") is True
    assert "inter-036-1" in deleted

    ui_logs = fs.get_file_logs("doc1")["logs"]
    assert ui_logs and any(lg.get("background") is True and lg.get("status") == "completed" for lg in ui_logs)
    modals_js = (Path(__file__).resolve().parents[1] / "src/tomtat/web/static/modules/modals.js").read_text(encoding="utf-8")
    assert "lg.background" in modals_js

    # Scenario 2: 400 unsupported background -> tự động fallback về sync store=False
    sync_fallback_calls = []

    class BadReq400(Exception):
        status_code = 400

    class FakeInteractionsUnsupported:
        def create(self, **kw):
            sync_fallback_calls.append(dict(kw))
            if kw.get("background"):
                raise BadReq400("400 INVALID_ARGUMENT: background parameter is not supported for this request")
            return SimpleNamespace(
                output_text='{"ok": true, "mode": "sync_fallback"}',
                usage=SimpleNamespace(total_input_tokens=5, total_output_tokens=3),
            )

        def get(self, *, id, timeout=None):
            raise AssertionError("should not poll")

        def delete(self, *, id, timeout=None):
            pass

        def cancel(self, *, id, timeout=None):
            pass

    monkeypatch.setattr(client, "_get_or_create_client", lambda key: SimpleNamespace(interactions=FakeInteractionsUnsupported()))
    res2 = client.generate_json(step="abstract", system="sys", prompt="prompt", schema={"type": "object"})
    assert res2.data == {"ok": True, "mode": "sync_fallback"}
    assert sync_fallback_calls[0].get("background") is True
    assert sync_fallback_calls[1].get("background") in (None, False)
    assert sync_fallback_calls[1].get("store") is False

    # Scenario 3: Hủy giữa lúc đang poll -> gọi interactions.cancel và ném LLMCancelledError
    cancelled_ids = []
    cancel_done = threading.Event()
    ev = threading.Event()

    class FakeInteractionsCancel:
        def create(self, **kw):
            return SimpleNamespace(id="inter-036-cancel", status="in_progress", output_text="")

        def get(self, *, id, timeout=None):
            ev.set()
            return SimpleNamespace(id=id, status="in_progress", output_text="")

        def cancel(self, *, id, timeout=None):
            cancelled_ids.append(id)
            cancel_done.set()

        def delete(self, *, id, timeout=None):
            pass

    monkeypatch.setattr(client, "_get_or_create_client", lambda key: SimpleNamespace(interactions=FakeInteractionsCancel()))
    try:
        with bind_cancel_event(ev):
            client.generate_json(
                step="abstract",
                system="sys",
                prompt="prompt",
                schema={"type": "object"},
            )
        assert False, "Expected LLMCancelledError"
    except LLMCancelledError:
        pass
    assert cancel_done.wait(timeout=2.0)
    assert "inter-036-cancel" in cancelled_ids


def test_bug_036_504_deadline_exceeded_fast_fallback_and_shared_outage_cooldown(monkeypatch, tmp_path):
    import yaml
    from pathlib import Path
    from types import SimpleNamespace
    from tomtat.llm.gemini import GeminiClient

    monkeypatch.setattr("tomtat.llm._get_quota_cycle_date", lambda: "2026-01-01")
    cfg = yaml.safe_load((Path(__file__).resolve().parents[1] / "config.yaml").read_text(encoding="utf-8"))
    cfg["paths"] = {"work": str(tmp_path / "work")}
    cfg["gemini"]["models_chain"] = ["gemini-4-hydrogen", "gemini-3.8-flash"]
    cfg["gemini"]["timeout_s_by_model"] = {"gemini-4-hydrogen": 600, "gemini-3.8-flash": 420}
    cfg["gemini"]["min_interval_s"] = 0.0
    cfg["gemini"]["rpm_limits"] = {}
    cfg["gemini"]["backoff_min_s"] = 0.01
    cfg["gemini"]["backoff_max_s"] = 1.0  # > 0.5 để bật outage tracking dùng chung
    cfg["gemini"]["retry_delay_base_s"] = 0.0
    cfg["gemini"]["background_poll_interval_s"] = 0.01
    cfg["gemini"]["model_timeout_outage_threshold"] = 2
    cfg["gemini"]["model_timeout_outage_cooldown_s"] = 180

    client = GeminiClient(cfg, ["AIzaFakeKeyA", "AIzaFakeKeyB"])

    model_calls = []

    class FakeInteractions504ThenOk:
        def create(self, **kw):
            model = kw["model"]
            model_calls.append(model)
            if model == "gemini-4-hydrogen":
                return SimpleNamespace(id="inter-504", status="in_progress", output_text="")
            return SimpleNamespace(
                id="inter-ok",
                status="completed",
                output_text='{"summary": "ok"}',
                usage=SimpleNamespace(total_input_tokens=10, total_output_tokens=5),
            )

        def get(self, *, id, timeout=None):
            return SimpleNamespace(
                id=id,
                status="failed",
                errors=[{"code": "deadline_exceeded", "message": "Deadline expired before operation could complete."}],
            )

        def delete(self, *, id, timeout=None):
            pass

        def cancel(self, *, id, timeout=None):
            pass

    monkeypatch.setattr(client, "_get_or_create_client", lambda key: SimpleNamespace(interactions=FakeInteractions504ThenOk()))

    # Lần gọi #1: background 504 trên gemini-4-hydrogen -> fallback NGAY sang gemini-3.8-flash (không retry 600-810s nữa)
    out1 = client.generate_json(step="abstract", system="sys", prompt="p1", schema={"type": "object"})
    assert out1.data == {"summary": "ok"}
    assert out1.model == "gemini-3.8-flash"
    assert model_calls.count("gemini-4-hydrogen") == 1
    assert "gemini-3.8-flash" in model_calls

    # Lần gọi #2: tiếp tục 504 trên gemini-4-hydrogen -> đủ threshold=2 -> kích hoạt outage cooldown dùng chung
    model_calls.clear()
    out2 = client.generate_json(step="abstract", system="sys", prompt="p2", schema={"type": "object"})
    assert out2.data == {"summary": "ok"}
    assert model_calls.count("gemini-4-hydrogen") == 1
    assert client._model_outage_until.get("gemini-4-hydrogen", 0.0) > 0.0

    # Lần gọi #3 (ví dụ lần tạo nháp 2 hoặc luồng khác): bỏ qua luôn gemini-4-hydrogen trong thời gian cooldown
    model_calls.clear()
    out3 = client.generate_json(step="abstract", system="sys", prompt="p3", schema={"type": "object"})
    assert out3.data == {"summary": "ok"}
    assert "gemini-4-hydrogen" not in model_calls
    assert model_calls == ["gemini-3.8-flash"]

    # Scenario phụ: chế độ sync-only (không bật background) gặp 504 trực tiếp ở model 600s cũng fallback ngay
    client._model_outage_until.clear()
    client._model_timeout_streak.clear()
    model_calls.clear()

    class Err504(Exception):
        status_code = 504

    class FakeSync504ThenOk:
        def create(self, **kw):
            model = kw["model"]
            model_calls.append(model)
            if model == "gemini-4-hydrogen":
                raise Err504(
                    "InternalServerError: Error code: 504 - {'error': {'message': "
                    "'Deadline expired before operation could complete.', 'code': 'deadline_exceeded'}}"
                )
            return SimpleNamespace(
                output_text='{"summary": "sync_ok"}',
                usage=SimpleNamespace(total_input_tokens=8, total_output_tokens=4),
            )

    monkeypatch.setattr(client, "_get_or_create_client", lambda key: SimpleNamespace(interactions=FakeSync504ThenOk()))
    out4 = client.generate_json(step="abstract", system="sys", prompt="p4", schema={"type": "object"})
    assert out4.data == {"summary": "sync_ok"}
    assert out4.model == "gemini-3.8-flash"
    assert model_calls.count("gemini-4-hydrogen") == 1
    assert "gemini-3.8-flash" in model_calls


# ═══════════════════════════════════════════════════════════════════════════════
# BUG-037: Vượt RPM trên gemini-4-hydrogen (lệch cửa sổ 60s khi restart, continuation hop, probe)
#          và chênh lệch đối soát QPS / 24h / Project giữa Web UI với Google AI Studio
# ═══════════════════════════════════════════════════════════════════════════════

def test_bug_037_rpm_sliding_window_persists_across_restart_and_log_reconciliation(tmp_path, monkeypatch):
    """BUG-037: Cửa sổ trượt RPM (~62.5s) phải bền vững qua khởi động lại (recent_rpm_ts) và
    tự khôi phục từ log khi server sập giữa chừng; log skipped không chiếm RPM."""
    import json
    import time as _t
    from tomtat.llm.gemini import GeminiClient

    monkeypatch.setattr("tomtat.llm._get_quota_cycle_date", lambda: "2026-01-01")
    keys = ["AIzaTestKey_RpmWin_0001"]
    monkeypatch.setattr("tomtat.config.get_api_keys", lambda root=None: keys)

    cfg = load_config()
    cfg["paths"] = {"work": str(tmp_path / "work")}
    cfg["gemini"]["min_interval_s"] = 0.0  # cô lập để kiểm tra riêng cửa sổ trượt 60s+ RPM
    cfg["gemini"]["rpm_limits"] = {"gemini-4-hydrogen": 5}

    # 1. Đặt đủ 5 lượt trong 60s ở client1 -> lưu recent_rpm_ts -> client2 (sau restart) phải chặn >= 60s
    client1 = GeminiClient(cfg, keys)
    for idx in range(5):
        k, _, w, _ = client1._acquire_paced_key_client_with_ts("gemini-4-hydrogen")
        assert k == keys[0]
        assert w == 0.0, f"Lượt {idx + 1}/5 đáng lẽ chạy ngay"

    saved = json.loads(client1._exhausted_file.read_text(encoding="utf-8"))
    assert saved.get("recent_rpm_ts"), "quota_exhausted.json phải lưu recent_rpm_ts"

    client2 = GeminiClient(cfg, keys)
    _, _, w6, _ = client2._acquire_paced_key_client_with_ts("gemini-4-hydrogen")
    assert w6 >= 60.0, f"Sau restart client2 phải giữ trọn cửa sổ RPM > 60s, nhưng wait={w6:.2f}s"

    # 2. Mô phỏng sập server trước khi lưu quota_exhausted.json -> khôi phục từ started_epoch trong log
    client2._exhausted_file.unlink(missing_ok=True)
    log_dir = tmp_path / "work" / "D01" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    now = _t.time()
    for i in range(5):
        lp = log_dir / f"20260101-16000{i}_000{i}_label.json"
        lp.write_text(
            json.dumps({
                "model": "gemini-4-hydrogen",
                "key_label": "Key #1 (...0001)",
                "started_at": "2026-01-01T16:00:00",
                "started_epoch": now - 6.0 + i * 0.4,
                "in_progress": False,
                "status": "completed",
            }),
            encoding="utf-8",
        )
    # Bản ghi bị skipped khi chờ không được tính chiếm slot RPM
    (log_dir / "20260101-160009_0009_label.json").write_text(
        json.dumps({
            "model": "gemini-4-hydrogen",
            "key_label": "Key #1 (...0001)",
            "started_at": "2026-01-01T16:00:09",
            "started_epoch": now - 1.0,
            "in_progress": False,
            "status": "skipped",
            "action": "requeue_after_wait",
        }),
        encoding="utf-8",
    )

    client3 = GeminiClient(cfg, keys)
    assert len(client3._model_rpm_tracker.get((keys[0], "gemini-4-hydrogen"), [])) == 5
    _, _, w_crash, _ = client3._acquire_paced_key_client_with_ts("gemini-4-hydrogen")
    assert w_crash >= 53.0, f"Khôi phục từ log phải đợi hết cửa sổ RPM còn lại (~56s), nhận {w_crash:.2f}s"


def test_bug_037_paced_continuation_hops_and_probe_rpm_protection(tmp_path, monkeypatch):
    """BUG-037: Khi SDK hỗ trợ automatic_continuation, client phải tắt tự động bắn hop liên thanh,
    tự chờ min_interval_s giữa các continuation hop, đếm đủ số hop vào RPM/RPD (kể cả khi hop 2 lỗi),
    và probe_key_model phải tuân thủ RPM pacing + không ghi trùng timestamp."""
    import json
    import time as _t
    from types import SimpleNamespace
    from tomtat.llm.base import get_key_rpd_details
    from tomtat.llm.gemini import GeminiClient

    monkeypatch.setattr("tomtat.llm._get_quota_cycle_date", lambda: "2026-01-01")
    keys = ["AIzaTestKey_Cont_1111"]
    monkeypatch.setattr("tomtat.config.get_api_keys", lambda root=None: keys)

    cfg = load_config()
    cfg["paths"] = {"work": str(tmp_path / "work")}
    cfg["gemini"]["model"] = "gemini-4-hydrogen"
    cfg["gemini"]["fallback_models"] = []
    cfg["gemini"]["models_chain"] = ["gemini-4-hydrogen"]
    cfg["gemini"]["max_rounds_per_model"] = 1
    cfg["gemini"]["min_interval_s"] = {"gemini-4-hydrogen": 0.18}
    cfg["gemini"]["rpm_limits"] = {"gemini-4-hydrogen": 5}
    cfg["gemini"]["backoff_base_s"] = 0.0
    cfg["gemini"]["backoff_max_s"] = 0.0
    cfg["gemini"]["background_execution"] = "never"

    client = GeminiClient(cfg, keys)
    hop_calls: list[dict] = []

    class FakeSdkContInteractions:
        def __init__(self):
            self.fail_second_hop = False

        def create(self, *, automatic_continuation=None, **body):
            hop_calls.append({"t": _t.monotonic(), "auto": automatic_continuation, "body": dict(body)})
            if body.get("continuation_token") == "tok-hop-2":
                if self.fail_second_hop:
                    class Err503(Exception):
                        status_code = 503
                    raise Err503("503 high demand on hop 2")
                return SimpleNamespace(
                    id="inter-hop-2",
                    status="completed",
                    output_text="true}",
                    usage=SimpleNamespace(total_input_tokens=5, total_output_tokens=3),
                    continuation_ids=["inter-hop-1", "inter-hop-2"],
                )
            return SimpleNamespace(
                id="inter-hop-1",
                status="requires_continuation",
                continuation_token="tok-hop-2",
                output_text='{"ok": ',
                usage=SimpleNamespace(total_input_tokens=5, total_output_tokens=2),
                continuation_ids=["inter-hop-1"],
            )

    fake_inter = FakeSdkContInteractions()
    monkeypatch.setattr(client, "_get_or_create_client", lambda k: SimpleNamespace(interactions=fake_inter))

    # 1. Thành công qua 2 continuation hop -> giãn nhịp >= 0.16s giữa 2 hop và cộng đủ 2 OK RPD
    log_dir = tmp_path / "work" / "C01" / "logs"
    res = client.generate_json(step="label", system="sys", prompt="p1", schema={}, log_dir=log_dir, attempt=1)
    assert res.data == {"ok": True}
    assert len(hop_calls) == 2
    assert hop_calls[0]["auto"] is False and hop_calls[1]["auto"] is False
    assert hop_calls[1]["body"].get("continuation_token") == "tok-hop-2"
    assert hop_calls[1]["t"] - hop_calls[0]["t"] >= 0.15
    assert client._key_ok_counts[(keys[0], "gemini-4-hydrogen")] == 2
    assert client._key_rpd_counts[(keys[0], "gemini-4-hydrogen")] == 2
    assert len(client._model_rpm_tracker[(keys[0], "gemini-4-hydrogen")]) == 2

    # 2. Hop 2 gặp 503 -> hop 1 hoàn tất vẫn được tính OK (1) + hop 2 lỗi tính transient (1), tổng thêm 2
    hop_calls.clear()
    fake_inter.fail_second_hop = True
    try:
        client.generate_json(step="label", system="sys", prompt="p2", schema={}, log_dir=log_dir, attempt=2)
    except Exception:
        pass
    assert client._key_ok_counts[(keys[0], "gemini-4-hydrogen")] == 3
    assert client._key_transient_counts[(keys[0], "gemini-4-hydrogen")] == 1
    assert client._key_rpd_counts[(keys[0], "gemini-4-hydrogen")] == 4

    details = get_key_rpd_details(cfg, api_keys=keys)
    cell = details["keys"][0]["models"]["gemini-4-hydrogen"]
    assert cell["ok_used"] == 3
    assert cell["transient_used"] == 1
    assert cell["used"] == 4

    # 3. probe_key_model tuân thủ RPM pacing (min_interval_s) và không đếm trùng timestamp vào tracker
    probe_times: list[float] = []

    class FakeProbeInteractions:
        def __init__(self):
            self.n = 0

        def create(self, *, automatic_continuation=None, **body):
            self.n += 1
            probe_times.append(_t.monotonic())
            if self.n == 2:
                class Err429Qps(Exception):
                    status_code = 429
                raise Err429Qps("429 query-per-second limit on near-identical request payloads")
            return SimpleNamespace(output_text="OK", status="completed")

    fake_probe = FakeProbeInteractions()
    cfg_probe = dict(cfg)
    cfg_probe["paths"] = {"work": str(tmp_path / "work_probe")}
    client_probe = GeminiClient(cfg_probe, keys)
    client_probe._key_qps_counts.clear()
    monkeypatch.setattr(client_probe, "_get_or_create_client", lambda k: SimpleNamespace(interactions=fake_probe))

    r1 = client_probe.probe_key_model(keys[0], "gemini-4-hydrogen")
    r2 = client_probe.probe_key_model(keys[0], "gemini-4-hydrogen")
    assert r1["outcome"] == "available"
    assert r2["outcome"] == "rate_limited_qps"
    assert len(probe_times) == 2
    assert probe_times[1] - probe_times[0] >= 0.15
    assert len(client_probe._model_rpm_tracker[(keys[0], "gemini-4-hydrogen")]) == 2
    assert client_probe._key_qps_counts[(keys[0], "gemini-4-hydrogen")] == 1


def test_bug_037_qps_429_surfaced_in_rpd_details_ui_and_cli(tmp_path, monkeypatch):
    """BUG-037: Web/API/CLI giữ nguyên `used` tương thích cũ nhưng phải công khai `qps_429_count` và
    `total_sent_with_qps` ở cả ma trận lẫn banner giải thích chênh lệch 24h / QPS / Project."""
    import json
    from tomtat.llm.base import get_key_rpd_details
    from tomtat.web import create_app

    monkeypatch.setattr("tomtat.llm._get_quota_cycle_date", lambda: "2026-01-01")
    keys = ["AIzaTestKey_Qps_AAAA", "AIzaTestKey_Qps_BBBB"]
    monkeypatch.setattr("tomtat.config.get_api_keys", lambda root=None: keys)

    cfg = load_config()
    cfg["paths"] = {
        "input": str(tmp_path / "input"),
        "output": str(tmp_path / "output"),
        "work": str(tmp_path / "work"),
        "prompts": str(cfg.prompts_dir),
        "report": str(tmp_path / "output" / "baocao.xlsx"),
    }

    log_dir = tmp_path / "work" / "Q01" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    # Key #1 trên 3.6-flash gặp 1 lần 429 QPS; trên 3.8-flash gặp 1 lần 503 + 1 lần QPS
    (log_dir / "20260101-160001_0001_extract.json").write_text(
        json.dumps({
            "model": "gemini-3.6-flash",
            "key_label": "Key #1 (...AAAA)",
            "started_at": "2026-01-01T16:00:01",
            "in_progress": False,
            "status": "error",
            "action": "backoff_payload_qps",
            "error": "429 query-per-second limit",
        }),
        encoding="utf-8",
    )
    (log_dir / "20260101-160002_0002_extract.json").write_text(
        json.dumps({
            "model": "gemini-3.8-flash",
            "key_label": "Key #1 (...AAAA)",
            "started_at": "2026-01-01T16:00:02",
            "in_progress": False,
            "status": "error",
            "action": "next_key",
            "error": "503 high demand",
        }),
        encoding="utf-8",
    )
    (log_dir / "20260101-160003_0003_extract.json").write_text(
        json.dumps({
            "model": "gemini-3.8-flash",
            "key_label": "Key #1 (...AAAA)",
            "started_at": "2026-01-01T16:00:03",
            "in_progress": False,
            "status": "error",
            "action": "backoff_payload_qps",
            "error": "429 near-identical request payloads",
        }),
        encoding="utf-8",
    )

    details = get_key_rpd_details(cfg, api_keys=keys)
    c36 = details["keys"][0]["models"]["gemini-3.6-flash"]
    assert c36["used"] == 0
    assert c36["qps_429_count"] == 1
    assert c36["total_sent_with_qps"] == 1

    c38 = details["keys"][0]["models"]["gemini-3.8-flash"]
    assert c38["used"] == 1
    assert c38["qps_429_count"] == 1
    assert c38["total_sent_with_qps"] == 2
    assert details["keys"][0]["total_qps_429"] == 2
    assert details["keys"][0]["total_sent_with_qps"] == 3

    models_by_name = {m["model"]: m for m in details["models"]}
    assert models_by_name["gemini-3.6-flash"]["qps_429_used"] == 1
    assert models_by_name["gemini-3.6-flash"]["total_sent_with_qps"] == 1

    app = create_app(cfg, mock=True)
    d_api = app.test_client().get("/api/rpd/keys").get_json()
    assert d_api["keys"][0]["models"]["gemini-3.6-flash"]["total_sent_with_qps"] == 1

    modals_js = (PROJECT_ROOT / "src" / "tomtat" / "web" / "static" / "modules" / "modals.js").read_text(encoding="utf-8")
    assert "total_sent_with_qps" in modals_js
    assert "qps_429_count" in modals_js
    assert "24 giờ trượt" in modals_js
    assert "Project" in modals_js

















