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

    from tomtat.models import CheckResult, Issue, LLMResult, LLMError, UnitItem
    assert CheckResult is not None
    assert Issue is not None
    assert LLMResult is not None
    assert LLMError is not None
    u = UnitItem(id=1, text="Câu mẫu.", words=2)
    assert u.id == 1 and u.words == 2

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
