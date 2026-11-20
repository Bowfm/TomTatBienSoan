"""Kiểm thử tích hợp đầu-cuối (End-to-End Integration Tests) cho tomtat."""
from __future__ import annotations

from tomtat import STYLES
from tomtat.config import load_config
from tomtat.llm import MockClient
from tomtat.pipeline import Pipeline
from tomtat.services import FileService
from tomtat.web import create_app


def test_full_pipeline_and_services_flow(tmp_path):
    """Kiểm thử toàn diện luồng: nạp file -> chạy AI -> duyệt -> xuất .docx."""
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

    # 1. Chạy qua FileService và Pipeline
    service = FileService(cfg)
    client = MockClient(cfg)
    res = service.process(inp, client, force=True)
    assert res["skipped"] is False
    assert res["state"]["status"] == "da_co_nhap"

    # 2. Kiểm tra duyệt bài qua Service
    appr_res = service.approve_and_export(
        stem="M54629",
        style=res["state"]["label"]["value"],
        extract=res["state"]["extract"]["text"],
        abstract=res["state"]["abstract"]["text"],
        confirmed_warnings=True,
    )
    assert appr_res["ok"] is True
    assert (tmp_path / "output" / "M54629.docx").exists()

    # 3. Kiểm tra qua Web Test Client
    app = create_app(cfg, mock=True)
    tc = app.test_client()
    r = tc.get("/api/files")
    assert r.status_code == 200
    assert len(r.get_json()["files"]) == 1
