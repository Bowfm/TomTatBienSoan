"""Kiểm thử tầng LLM: xoay vòng API keys, chu kỳ reset quota Pacific Time, transient error."""
from __future__ import annotations

import json
from tomtat.config import load_config
from tomtat.llm import GeminiClient, _get_quota_cycle_date, is_transient


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
