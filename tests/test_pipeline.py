"""Kiểm thử tầng Pipeline: luồng chạy toàn diện, kế thừa thông minh, xử lý bước dở dang."""
from __future__ import annotations

from datetime import datetime
import io
import zipfile

from tomtat import STYLES
from tomtat.config import load_config
from tomtat.llm import MockClient
from tomtat.pipeline import Pipeline, process_file
from tomtat.state import FileWorkspace
from tomtat.web import create_app


def test_pipeline_and_web_api_on_sample(tmp_path):
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

    client = MockClient(cfg)
    pipeline = Pipeline(cfg, client)
    out = pipeline.process(inp, force=True)
    assert out["skipped"] is False
    st = out["state"]
    assert st["label"]["value"] in STYLES
    assert st["checks"]["ok"] is True

    app = create_app(cfg, mock=True)
    tc = app.test_client()
    r_files = tc.get("/api/files")
    assert r_files.status_code == 200
    assert len(r_files.get_json()["files"]) == 1

    r_file = tc.get("/api/file/M54629")
    assert r_file.status_code == 200
    f_json = r_file.get_json()
    assert f_json["state"]["stem"] == "M54629"

    # Kiểm tra duyệt và xuất qua API
    r_app = tc.post("/api/file/M54629/approve", json={"confirm_warnings": True})
    assert r_app.status_code == 200
    app_json = r_app.get_json()
    assert app_json["ok"] is True
    assert (tmp_path / "output" / "M54629.docx").exists()

    # Kiểm tra tải file ZIP
    r_zip = tc.get("/api/download-zip?stems=M54629")
    assert r_zip.status_code == 200
    expected_zip = f"{datetime.now().strftime('%d-%m')}.zip"
    assert expected_zip in r_zip.headers.get("Content-Disposition", "")
    with zipfile.ZipFile(io.BytesIO(r_zip.data)) as zf:
        assert "M54629.docx" in zf.namelist()
    assert (tmp_path / "output" / expected_zip).exists()

    # Kiểm tra tải lẻ 1 file đã xong
    r_single = tc.get("/api/download-file/M54629")
    assert r_single.status_code == 200

    # Kiểm tra tải file mới lên qua nút "+ Thêm file .docx" (/api/upload) và chạy AI ngay
    r_up = tc.post(
        "/api/upload",
        data={
            "files": (io.BytesIO(sample.read_bytes()), "M99999.docx"),
            "run_now": "true",
            "mock": "true",
        },
        content_type="multipart/form-data",
    )
    assert r_up.status_code == 200
    up_json = r_up.get_json()
    assert up_json["ok"] is True
    assert "M99999.docx" in up_json["added"]
    assert (tmp_path / "input" / "M99999.docx").exists()
    r_new = tc.get("/api/file/M99999").get_json()
    assert r_new["state"]["status"] == "da_co_nhap"

    # Kiểm tra hỏi xác nhận khi tải trùng tên file mà chưa bật overwrite
    r_dup = tc.post(
        "/api/upload",
        data={
            "files": (io.BytesIO(sample.read_bytes()), "M99999.docx"),
            "run_now": "false",
            "overwrite": "false",
        },
        content_type="multipart/form-data",
    ).get_json()
    assert r_dup.get("need_overwrite") is True


def test_process_file_resume_partial(tmp_path):
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

    client = MockClient(cfg)

    # 1. Chạy chỉ bước label và extract
    process_file(inp, cfg, client, only_step="label")
    process_file(inp, cfg, client, only_step="extract")

    ws = FileWorkspace(cfg.work_dir, inp)
    st1 = ws.load_state()
    saved_label = st1["label"]["value"]
    saved_extract = st1["extract"]["text"]
    assert saved_label
    assert saved_extract
    assert not st1["abstract"]["text"]

    # 2. Chạy tiếp process_file bình thường -> giữ nguyên label & extract, chỉ chạy tiếp abstract
    out2 = process_file(inp, cfg, client, force=True, restart=False)
    st2 = out2["state"]
    assert st2["label"]["value"] == saved_label
    assert st2["extract"]["text"] == saved_extract
    assert st2["abstract"]["text"]
    assert st2["status"] == "da_co_nhap"


def test_incomplete_step_discarded_and_rerun(tmp_path):
    """Kiểm tra: nếu một bước mới làm dở, bước đó bị coi là chưa làm gì và làm lại từ đầu."""
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

    client = MockClient(cfg)
    ws = FileWorkspace(cfg.work_dir, inp)

    # 1. Giả lập: label đã hoàn thành 100%, nhưng extract mới làm dở
    process_file(inp, cfg, client, only_step="label")
    st = ws.load_state()
    assert st["label"]["value"]
    st["extract"] = {"text": "doan van do dang...", "selections": {}, "words": 0}
    ws.save_state(st)

    # 2. Chạy tiếp process_file: extract dở dang phải bị xóa và chạy lại 100% từ đầu
    out = process_file(inp, cfg, client, force=True, restart=False)
    st_res = out["state"]
    assert st_res["extract"]["words"] > 0
    assert len(st_res["extract"]["selections"]) > 0
    assert st_res["abstract"]["text"]
    assert st_res["status"] == "da_co_nhap"
