"""Kiểm thử tầng LLM: xoay vòng API keys, chu kỳ reset quota Pacific Time, transient error."""
from __future__ import annotations

import json
from tomtat.config import load_config
from tomtat.llm import GeminiClient, _get_quota_cycle_date, get_rpd_status, is_transient
from tomtat.web import create_app


def test_transient_error_detection():
    class Fake429Error(Exception):
        status_code = 429

    class Fake400Error(Exception):
        status_code = 400

    assert is_transient(Fake429Error()) is True
    assert is_transient(TimeoutError()) is True
    assert is_transient(Fake400Error()) is False


def test_get_api_keys_multi_format(tmp_path, monkeypatch):
    import tomtat.config
    from tomtat.config import get_api_keys

    # Tránh nạp file api_keys.txt thật của dự án trong lúc test
    monkeypatch.setattr(tomtat.config, "PROJECT_ROOT", tmp_path)

    # Kiểm tra tách bằng dấu phẩy
    monkeypatch.setenv("GEMINI_API_KEY", "key_a, key_b, key_c")
    keys = get_api_keys()
    assert keys == ["key_a", "key_b", "key_c"]

    # Kiểm tra biến GEMINI_API_KEY_1, GEMINI_API_KEY_2
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY_1", "key_one")
    monkeypatch.setenv("GEMINI_API_KEY_2", "key_two")
    keys = get_api_keys()
    assert keys == ["key_one", "key_two"]

    # Kiểm tra nạp từ api_keys.txt
    monkeypatch.delenv("GEMINI_API_KEY_1", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY_2", raising=False)
    (tmp_path / "api_keys.txt").write_text("key_file_1\n# comment\nkey_file_2\n", encoding="utf-8")
    keys = get_api_keys()
    assert keys == ["key_file_1", "key_file_2"]


def test_quota_cycle_pacific_time(tmp_path, monkeypatch):
    today_pt = _get_quota_cycle_date()
    assert len(today_pt) == 10 and today_pt[4] == "-" and today_pt[7] == "-"

    cfg = load_config()
    cfg["paths"] = {"work": str(tmp_path / "work")}

    client = GeminiClient(cfg, ["dummy_key_1", "dummy_key_2"])

    # 1. Đánh dấu cạn hạn mức cho key 1 với model gemini-4-hydrogen
    client._mark_exhausted("dummy_key_1", "gemini-4-hydrogen")
    assert ("dummy_key_1", "gemini-4-hydrogen") in client._exhausted

    # File quota_exhausted.json được tạo với date theo Pacific Time
    data = json.loads(client._exhausted_file.read_text(encoding="utf-8"))
    assert data["date"] == today_pt
    assert len(data["exhausted"]) == 1

    # 2. Key 2 vẫn dùng được cho gemini-4-hydrogen
    k, _ = client._get_available_key_client("gemini-4-hydrogen")
    assert k == "dummy_key_2"

    # Đánh dấu cạn tiếp key 2
    client._mark_exhausted("dummy_key_2", "gemini-4-hydrogen")
    k, _ = client._get_available_key_client("gemini-4-hydrogen")
    assert k is None

    # Model fallback vẫn dùng được vì chưa cạn
    k, _ = client._get_available_key_client("gemini-3.8-flash")
    assert k in ("dummy_key_1", "dummy_key_2")

    # 3. Giả lập sang ngày mới ở Pacific Time (15:00 VN):
    monkeypatch.setattr("tomtat.llm._get_quota_cycle_date", lambda: "2099-12-31")
    k, _ = client._get_available_key_client("gemini-4-hydrogen")
    assert k is not None
    assert len(client._exhausted) == 0


def test_rpd_tracking_and_web_api(tmp_path, monkeypatch):
    cfg = load_config()
    cfg["paths"] = {
        "input": str(tmp_path / "input"),
        "output": str(tmp_path / "output"),
        "work": str(tmp_path / "work"),
        "prompts": str(cfg.prompts_dir),
        "report": str(tmp_path / "output" / "baocao.xlsx"),
    }

    keys = ["dummy_key_1", "dummy_key_2"]
    client = GeminiClient(cfg, keys)

    # Ghi nhận 3 request cho gemini-4-hydrogen và 12 request cho gemini-3.5-flash-lite
    for _ in range(3):
        client._record_request("dummy_key_1", "gemini-4-hydrogen")
    for _ in range(12):
        client._record_request("dummy_key_2", "gemini-3.5-flash-lite")

    rpd_list = get_rpd_status(cfg, api_keys=keys)
    by_model = {item["model"]: item for item in rpd_list}

    # Với 2 keys: gemini-4-hydrogen có limit 20*2=40, gemini-3.5-flash-lite có limit 500*2=1000
    assert by_model["gemini-4-hydrogen"]["used"] == 3
    assert by_model["gemini-4-hydrogen"]["limit"] == 40
    assert by_model["gemini-4-hydrogen"]["display"] == "gemini-4-hydrogen: 3/40"

    assert by_model["gemini-3.5-flash-lite"]["used"] == 12
    assert by_model["gemini-3.5-flash-lite"]["limit"] == 1000
    assert by_model["gemini-3.5-flash-lite"]["display"] == "gemini-3.5-flash-lite: 12/1000"

    assert by_model["gemini-3.8-flash"]["display"] == "gemini-3.8-flash: 0/40"

    # Kiểm tra qua Web API (/api/files, /api/rpd, /api/batch/status) và HTML
    monkeypatch.setattr("tomtat.config.get_api_keys", lambda: keys)
    app = create_app(cfg, mock=True)
    tc = app.test_client()

    r_html = tc.get("/")
    assert r_html.status_code == 200
    html_text = r_html.get_data(as_text=True)
    assert 'id="rpd-bar"' in html_text
    assert 'id="rpd-models-list"' in html_text

    r_rpd = tc.get("/api/rpd")
    assert r_rpd.status_code == 200
    api_rpd_map = {x["model"]: x for x in r_rpd.get_json()["rpd_status"]}
    assert api_rpd_map["gemini-4-hydrogen"]["display"] == "gemini-4-hydrogen: 3/40"

    r_files = tc.get("/api/files")
    assert r_files.status_code == 200
    assert "rpd_status" in r_files.get_json()

    r_batch = tc.get("/api/batch/status")
    assert r_batch.status_code == 200
    assert "rpd_status" in r_batch.get_json()

    # Sang ngày mới -> bộ đếm RPD tự reset về 0
    monkeypatch.setattr("tomtat.llm._get_quota_cycle_date", lambda: "2099-12-31")
    reset_list = get_rpd_status(cfg, api_keys=keys)
    assert all(x["used"] == 0 for x in reset_list)


def test_rpm_pacing_for_5_rpm_models(tmp_path):
    from tomtat.llm import get_model_min_interval_s, get_model_rpm_per_key

    cfg = load_config()
    cfg["paths"] = {"work": str(tmp_path / "work")}

    for m5 in ("gemini-4-hydrogen", "gemini-3.8-flash", "gemini-3.6-flash"):
        assert get_model_rpm_per_key(m5, cfg) == 5
        assert get_model_min_interval_s(m5, cfg) >= 12.0

    for m15 in ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite"):
        assert get_model_rpm_per_key(m15, cfg) == 15
        assert get_model_min_interval_s(m15, cfg) >= 4.0

    # Kiểm tra với 1 key duy nhất: lần gọi thứ 2 liên tiếp trên cùng model 5 RPM phải được xếp cách nhau >= 13s
    client = GeminiClient(cfg, ["single_key"])
    k1, _, wait1 = client._acquire_paced_key_client("gemini-4-hydrogen")
    assert k1 == "single_key"
    assert wait1 == 0.0

    k2, _, wait2 = client._acquire_paced_key_client("gemini-4-hydrogen")
    assert k2 == "single_key"
    assert wait2 >= 12.5

    # Lần thứ 3 xếp nối đuôi tiếp (>= 25s) để các luồng song song không bao giờ dồn cục vượt 5 RPM
    k3, _, wait3 = client._acquire_paced_key_client("gemini-4-hydrogen")
    assert k3 == "single_key"
    assert wait3 >= 25.0


def test_dynamic_pool_scaling_when_adding_keys(tmp_path, monkeypatch):
    import tomtat.config
    from tomtat.config import get_pool_scaling

    cfg = load_config()
    cfg["workers"] = "auto"
    cfg.pop("max_workers", None)  # cô lập: test này kiểm tra công thức auto, độc lập với trần cứng (BUG-014)
    monkeypatch.setattr(tomtat.config, "PROJECT_ROOT", tmp_path)
    cfg["paths"] = {
        "input": str(tmp_path / "input"),
        "output": str(tmp_path / "output"),
        "work": str(tmp_path / "work"),
        "prompts": str(cfg.prompts_dir),
        "report": str(tmp_path / "output" / "baocao.xlsx"),
    }

    # 1. Ban đầu có 60 keys
    keys_60 = [f"key_{i}" for i in range(1, 61)]
    (tmp_path / "api_keys.txt").write_text("\n".join(keys_60), encoding="utf-8")

    s60 = get_pool_scaling(cfg)
    assert s60["key_count"] == 60
    assert s60["recommended_workers"] == 6
    assert s60["max_concurrent_calls"] == 8

    client = GeminiClient(cfg, keys_60)
    assert len(client._api_keys) == 60

    # 2. Người dùng thêm 10 keys nữa vào api_keys.txt (tổng 70 keys)
    keys_70 = [f"key_{i}" for i in range(1, 71)]
    (tmp_path / "api_keys.txt").write_text("\n".join(keys_70), encoding="utf-8")

    s70 = get_pool_scaling(cfg)
    assert s70["key_count"] == 70
    assert s70["recommended_workers"] == 7
    assert s70["max_concurrent_calls"] == 9

    # Client tự động sync nóng danh sách 70 keys mà không mất trạng thái
    client.sync_keys()
    assert len(client._api_keys) == 70

    # Hạn mức RPD tự động nhảy từ 1200 (60*20) lên 1400 (70*20) cho model 20 RPD
    # và từ 30000 (60*500) lên 35000 (70*500) cho model 500 RPD
    rpd_70 = {x["model"]: x for x in get_rpd_status(cfg)}
    assert rpd_70["gemini-4-hydrogen"]["limit"] == 1400
    assert rpd_70["gemini-3.8-flash"]["limit"] == 1400
    assert rpd_70["gemini-3.6-flash"]["limit"] == 1400
    assert rpd_70["gemini-3.5-flash-lite"]["limit"] == 35000
    assert rpd_70["gemini-3.1-flash-lite"]["limit"] == 35000


def test_rpd_counts_503_and_log_reconciliation(tmp_path, monkeypatch):
    """Kiểm tra: lỗi 503 (Service Unavailable / High Demand) vẫn phải được tính là 1 RPD."""
    cfg = load_config()
    cfg["paths"] = {
        "input": str(tmp_path / "input"),
        "output": str(tmp_path / "output"),
        "work": str(tmp_path / "work"),
        "prompts": str(cfg.prompts_dir),
        "report": str(tmp_path / "output" / "baocao.xlsx"),
    }
    cfg["gemini"]["min_interval_s"] = 0.0
    cfg["gemini"]["backoff_base_s"] = 0.01
    cfg["gemini"]["backoff_max_s"] = 0.02

    keys = ["key_1", "key_2"]
    monkeypatch.setattr("tomtat.config.get_api_keys", lambda root=None: keys)
    client = GeminiClient(cfg, keys)

    class Fake503Error(Exception):
        status_code = 503

        def __str__(self):
            return "Error code: 503 - {'error': {'message': 'high demand', 'code': 'service_unavailable'}}"

    call_history = []

    def fake_call_once(model_name, step, system, prompt, schema, client=None):
        call_history.append(model_name)
        if model_name == "gemini-4-hydrogen":
            raise Fake503Error()
        return '{"label": "hành chính – công vụ", "confidence": "cao", "reason": "ok"}', {}, "completed"

    monkeypatch.setattr(client, "_call_once", fake_call_once)

    res = client.generate_json(
        step="label",
        system="sys",
        prompt="prompt",
        schema={},
        log_dir=tmp_path / "work" / "M1" / "logs",
        attempt=1,
    )
    assert res.model == "gemini-3.8-flash"
    # 2 keys x 3 vòng (max_rounds_per_model=3): gemini-4-hydrogen bị 503 đủ 6 lần trước khi tạm hạ xuống gemini-3.8-flash
    assert call_history.count("gemini-4-hydrogen") == 6
    assert call_history.count("gemini-3.8-flash") == 1
    # 503 KHÔNG được coi là hết RPD
    assert not any(m == "gemini-4-hydrogen" for (_, m) in client._exhausted)

    rpd_map = {x["model"]: x for x in get_rpd_status(cfg, api_keys=keys)}
    # Cả 6 lần gọi 503 trên gemini-4-hydrogen đều phải được tính vào RPD!
    assert rpd_map["gemini-4-hydrogen"]["used"] == 6
    assert rpd_map["gemini-4-hydrogen"]["display"] == "gemini-4-hydrogen: 6/40"
    assert rpd_map["gemini-3.8-flash"]["used"] == 1
    assert rpd_map["gemini-3.8-flash"]["display"] == "gemini-3.8-flash: 1/40"


def _sequential_client(tmp_path, monkeypatch, n_keys):
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
    keys = [f"key_{i}" for i in range(1, n_keys + 1)]
    monkeypatch.setattr("tomtat.config.get_api_keys", lambda root=None: keys)
    monkeypatch.setattr("tomtat.llm.base.get_model_rpm_per_key", lambda m, c=None: 10_000)
    monkeypatch.setattr("tomtat.llm.gemini.get_model_rpm_per_key", lambda m, c=None: 10_000)
    return GeminiClient(cfg, keys), keys


class _Err(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.status_code = code


def test_sequential_key_order_and_429_exhaustion(tmp_path, monkeypatch):
    """Gọi tuần tự #1 -> #N, bước sau tiếp tục từ key kế tiếp, hết vòng thì quay lại #1; 429 3 lần mới bỏ (key, model)."""
    monkeypatch.setattr("tomtat.llm.gemini._COOLDOWN_S", 0.0)
    client, keys = _sequential_client(tmp_path, monkeypatch, 10)
    used: list[tuple[str, str]] = []
    plan: dict = {}

    def fake_call_once(model_name, step, system, prompt, schema, client=None):
        k = next(key for key, c in client_map.items() if c is client)
        used.append((model_name, k))
        outcome = plan.get((model_name, k), "503")
        if outcome == "ok":
            return '{"ok": true}', {}, "completed"
        if outcome == "429":
            raise _Err(429, "Rate limit exceeded for model (limit: 20 requests per day on Free Tier).")
        raise _Err(503, "high demand service_unavailable")

    client_map = {}

    def fake_get(k):
        client_map.setdefault(k, object())
        return client_map[k]

    monkeypatch.setattr(client, "_get_or_create_client", fake_get)
    monkeypatch.setattr(client, "_call_once", fake_call_once)
    H = "gemini-4-hydrogen"

    def call():
        return client.generate_json(step="s", system="", prompt="", schema={})

    # Bước 1: key_1..key_3 lỗi 503, key_4 thành công
    plan.update({(H, "key_4"): "ok"})
    assert call().model == H
    assert [k for _, k in used] == ["key_1", "key_2", "key_3", "key_4"]

    # Bước 2: bắt đầu từ key_5; key_5..key_10 lỗi, quay lại key_1 (key_1 bị 429 lần 1 -> chưa đánh dấu cạn), key_2 thành công
    used.clear()
    plan.clear()
    plan.update({(H, "key_1"): "429", (H, "key_2"): "ok"})
    assert call().model == H
    assert [k for _, k in used] == ["key_5", "key_6", "key_7", "key_8", "key_9", "key_10", "key_1", "key_2"]
    assert ("key_1", H) not in client._exhausted
    assert client._idle_429_strikes.get(("key_1", H)) == 1

    # Bước 3: tiếp tục từ key_3; mọi key bị 429 liên tiếp qua 3 vòng (key_1 đã có 1 lần từ trước nên hết ở vòng 2)
    used.clear()
    plan.clear()
    for k in keys:
        plan[(H, k)] = "429"
    plan[("gemini-3.8-flash", "key_1")] = "ok"
    assert call().model == "gemini-3.8-flash"
    h_keys = [k for m, k in used if m == H]
    assert h_keys.count("key_1") == 2  # thêm 2 lần ở bước 3 -> đủ 3 lần là bị loại
    assert h_keys.count("key_2") == 3
    assert all((k, H) in client._exhausted for k in keys)

    # Bước 4: không gọi 4 Hydrogen nữa
    used.clear()
    plan[("gemini-3.8-flash", "key_2")] = "ok"
    assert call().model == "gemini-3.8-flash"
    assert used == [("gemini-3.8-flash", "key_2")]


def test_project_quota_429_does_not_lock_keys_immediately(tmp_path, monkeypatch):
    """Lỗi 429 'Your project has exceeded a quota' chỉ cooldown tạm thời, không khóa sạch cả pool cả ngày."""
    client, keys = _sequential_client(tmp_path, monkeypatch, 4)
    monkeypatch.setattr("tomtat.llm.gemini._COOLDOWN_S", 0.01)
    used: list[tuple[str, str]] = []
    plan: dict = {
        ("gemini-4-hydrogen", "key_1"): "project_429",
        ("gemini-4-hydrogen", "key_2"): "project_429",
        ("gemini-4-hydrogen", "key_3"): "ok",
    }
    client_map = {}

    def fake_get(k):
        client_map.setdefault(k, object())
        return client_map[k]

    def fake_call_once(model_name, step, system, prompt, schema, client=None):
        k = next(key for key, c in client_map.items() if c is client)
        used.append((model_name, k))
        outcome = plan.get((model_name, k), "503")
        if outcome == "ok":
            return '{"ok": true}', {}, "completed"
        if outcome == "project_429":
            raise _Err(429, "Your project has exceeded a quota. See https://ai.dev/rate-limit")
        raise _Err(503, "high demand")

    monkeypatch.setattr(client, "_get_or_create_client", fake_get)
    monkeypatch.setattr(client, "_call_once", fake_call_once)

    res = client.generate_json(step="s", system="", prompt="", schema={})
    assert res.model == "gemini-4-hydrogen"
    assert used == [
        ("gemini-4-hydrogen", "key_1"),
        ("gemini-4-hydrogen", "key_2"),
        ("gemini-4-hydrogen", "key_3"),
    ]
    # key_1 và key_2 KHÔNG bị đánh dấu hết RPD cả ngày chỉ vì 1 lần project quota 429
    assert ("key_1", "gemini-4-hydrogen") not in client._exhausted
    assert ("key_2", "gemini-4-hydrogen") not in client._exhausted


def test_model_specific_timeout(tmp_path, monkeypatch):
    """Chỉ gemini-4-hydrogen dùng timeout 240s, các model khác giữ timeout_s mặc định (90s)."""
    from types import SimpleNamespace
    from tomtat.llm import GeminiClient, get_model_timeout_s

    cfg = {
        "paths": {"work": str(tmp_path)},
        "gemini": {
            "model": "gemini-4-hydrogen",
            "fallback_models": ["gemini-3.8-flash"],
            "timeout_s": 90,
            "timeout_s_by_model": {"gemini-4-hydrogen": 240},
        },
    }
    assert get_model_timeout_s("gemini-4-hydrogen", cfg) == 240.0
    assert get_model_timeout_s("gemini-3.8-flash", cfg) == 90.0

    monkeypatch.setattr("tomtat.llm._get_quota_cycle_date", lambda: "2026-01-01")
    client = GeminiClient(cfg, ["key_1"])
    seen_timeouts: list[tuple[str, float]] = []

    class FakeInteractions:
        def create(self, **kwargs):
            seen_timeouts.append((kwargs["model"], kwargs["timeout"]))
            return SimpleNamespace(output_text='{"ok": true}', usage=None, status="completed")

    fake_sdk_client = SimpleNamespace(interactions=FakeInteractions())
    client._call_once("gemini-4-hydrogen", "abstract", "sys", "prompt", {}, client=fake_sdk_client)
    client._call_once("gemini-3.8-flash", "abstract", "sys", "prompt", {}, client=fake_sdk_client)
    assert seen_timeouts == [("gemini-4-hydrogen", 240.0), ("gemini-3.8-flash", 90.0)]


def test_single_remaining_key_rpm_pacing_and_persistence(tmp_path, monkeypatch):
    """Khi 49/50 key bị đánh dấu cạn, 1 key còn lại phải được giãn nhịp RPM (kể cả qua client mới / file sau)."""
    monkeypatch.setattr("tomtat.llm._get_quota_cycle_date", lambda: "2026-01-01")
    keys = [f"key_{i}" for i in range(1, 51)]
    monkeypatch.setattr("tomtat.config.get_api_keys", lambda root=None: keys)

    cfg = load_config()
    cfg["paths"] = {"work": str(tmp_path / "work")}
    cfg["gemini"]["min_interval_s"] = {"gemini-4-hydrogen": 13.0}
    cfg["gemini"]["rpm_limits"] = {"gemini-4-hydrogen": 5}

    client1 = GeminiClient(cfg, keys)
    for k in keys[:-1]:
        client1._mark_exhausted(k, "gemini-4-hydrogen")

    # Lần gọi 1 trên key còn lại (key_50): chạy ngay (wait_s == 0)
    k1, _, w1 = client1._acquire_paced_key_client("gemini-4-hydrogen")
    assert k1 == "key_50"
    assert w1 == 0.0

    # Lần gọi 2 liên tiếp trên key_50: phải chờ >= 12.5s
    k2, _, w2 = client1._acquire_paced_key_client("gemini-4-hydrogen")
    assert k2 == "key_50"
    assert w2 >= 12.5

    # Khởi tạo client mới (giả lập file sau / restart): vẫn phải giữ lịch chờ của key_50
    client2 = GeminiClient(cfg, keys)
    k3, _, w3 = client2._acquire_paced_key_client("gemini-4-hydrogen")
    assert k3 == "key_50"
    assert w3 >= 24.0


def test_rpm_429_sets_cooldown_instead_of_exhausted(tmp_path, monkeypatch):
    """Lỗi 429 do RPM/TPM chỉ đặt cooldown tạm thời, không khóa vĩnh viễn key trong ngày."""
    client, _ = _sequential_client(tmp_path, monkeypatch, 2)
    H = "gemini-4-hydrogen"

    client_map = {}

    def fake_get(k):
        client_map.setdefault(k, object())
        return client_map[k]

    def fake_call_once(model_name, step, system, prompt, schema, client=None):
        k = next(key for key, c in client_map.items() if c is client)
        if k == "key_1":
            raise _Err(429, "Quota exceeded for quota metric 'GenerateRequestsPerMinutePerProjectPerModel'")
        return '{"ok": true}', {}, "completed"

    monkeypatch.setattr(client, "_get_or_create_client", fake_get)
    monkeypatch.setattr(client, "_call_once", fake_call_once)

    res = client.generate_json(step="s", system="", prompt="", schema={})
    assert res.model == H
    assert ("key_1", H) not in client._exhausted
    assert client._key_cooldown.get("key_1", 0.0) > 0.0


def test_three_429_strikes_per_key_and_model(tmp_path, monkeypatch):
    """Mỗi cặp (key, model) phải gặp lỗi 429 đủ 3 lần mới bị đánh dấu hết hạn mức; thành công thì reset bộ đếm."""
    monkeypatch.setattr("tomtat.llm.gemini._COOLDOWN_S", 0.0)
    monkeypatch.setattr("tomtat.llm._get_quota_cycle_date", lambda: "2026-01-01")
    client, keys = _sequential_client(tmp_path, monkeypatch, 2)
    H = "gemini-4-hydrogen"
    F = "gemini-3.8-flash"

    client_map = {}

    def fake_get(k):
        client_map.setdefault(k, object())
        return client_map[k]

    outcomes: dict[tuple[str, str], str] = {}

    def fake_call_once(model_name, step, system, prompt, schema, client=None):
        k = next(key for key, c in client_map.items() if c is client)
        act = outcomes.get((model_name, k), "ok")
        if act == "429_daily":
            raise _Err(429, "Rate limit exceeded for model (limit: 20 requests per day on Free Tier).")
        if act == "429_rpm":
            raise _Err(429, "Quota exceeded for quota metric 'GenerateRequestsPerMinutePerProjectPerModel'")
        return '{"ok": true}', {}, "completed"

    monkeypatch.setattr(client, "_get_or_create_client", fake_get)
    monkeypatch.setattr(client, "_call_once", fake_call_once)

    # Lần 1 & 2 bị 429 trên (key_1, H) -> chưa bị đánh dấu hết hạn mức
    outcomes[(H, "key_1")] = "429_daily"
    outcomes[(H, "key_2")] = "ok"
    client.generate_json(step="s", system="", prompt="", schema={})
    assert ("key_1", H) not in client._exhausted
    assert client._idle_429_strikes[("key_1", H)] == 1

    # Chuyển con trỏ về key_1 để test lần 2 trên (key_1, H)
    client._model_cursor[H] = 0
    outcomes[(H, "key_1")] = "429_rpm"
    client.generate_json(step="s", system="", prompt="", schema={})
    assert ("key_1", H) not in client._exhausted
    assert client._idle_429_strikes[("key_1", H)] == 2

    # Kiểm tra lưu & khôi phục strikes_429 qua lần khởi tạo client mới
    client_reloaded = GeminiClient(client.cfg, keys)
    monkeypatch.setattr(client_reloaded, "_get_or_create_client", fake_get)
    monkeypatch.setattr(client_reloaded, "_call_once", fake_call_once)
    assert client_reloaded._idle_429_strikes[("key_1", H)] == 2

    # Nếu gọi thành công trước lần 3 -> reset số lần 429 của (key_1, H) về 0
    client_reloaded._model_cursor[H] = 0
    outcomes[(H, "key_1")] = "ok"
    client_reloaded.generate_json(step="s", system="", prompt="", schema={})
    assert ("key_1", H) not in client_reloaded._idle_429_strikes
    assert ("key_1", H) not in client_reloaded._exhausted

    # Gặp 429 đủ 3 lần liên tiếp -> đánh dấu hết hạn mức chỉ cho (key_1, H), KHÔNG ảnh hưởng (key_1, F)
    outcomes[(H, "key_1")] = "429_daily"
    for _ in range(3):
        client_reloaded._model_cursor[H] = 0
        client_reloaded.generate_json(step="s", system="", prompt="", schema={})
    assert ("key_1", H) in client_reloaded._exhausted
    assert ("key_1", F) not in client_reloaded._exhausted


def test_incomplete_empty_output_logs_clear_reason_and_retries(tmp_path, monkeypatch):
    """Khi model chạm trần max_output_tokens trong lúc thinking (status=incomplete, output rỗng),
    log phải ghi rõ nguyên nhân thay vì chỉ báo JSONDecodeError chung chung và vẫn thử key kế tiếp."""
    client, _ = _sequential_client(tmp_path, monkeypatch, 2)
    log_dir = tmp_path / "logs"

    client_map = {}

    def fake_get(k):
        client_map.setdefault(k, object())
        return client_map[k]

    def fake_call_once(model_name, step, system, prompt, schema, client=None):
        k = next(key for key, c in client_map.items() if c is client)
        if k == "key_1":
            return "", {"total_input_tokens": 10000, "total_output_tokens": 3, "total_thought_tokens": 16381}, "incomplete"
        return '{"ok": true}', {"total_input_tokens": 10000, "total_output_tokens": 50, "total_thought_tokens": 500}, "completed"

    monkeypatch.setattr(client, "_get_or_create_client", fake_get)
    monkeypatch.setattr(client, "_call_once", fake_call_once)

    res = client.generate_json(step="abstract", system="", prompt="", schema={}, log_dir=log_dir)
    assert res.data == {"ok": True}

    logs = sorted(log_dir.glob("*.json"))
    assert len(logs) == 2
    first_log = json.loads(logs[0].read_text(encoding="utf-8"))
    assert first_log["status"] == "error"
    assert first_log["api_status"] == "incomplete"
    assert "max_output_tokens" in first_log["error"]
    assert "think=16381" in first_log["error"]



