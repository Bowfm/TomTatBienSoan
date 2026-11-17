"""Bộ kiểm thử đơn vị và tích hợp cho công cụ tomtat."""
from __future__ import annotations

from pathlib import Path
from docx import Document
from docx.oxml.ns import qn
import pytest

from tomtat import STYLES
from tomtat.checks import run_checks
from tomtat.config import load_config
from tomtat.docx_io import (
    DocxError,
    check_filename_convention,
    detect_layout,
    fill_docx,
    load_table,
    row_text,
)
from tomtat.llm import MockClient, is_transient
from tomtat.pipeline import process_file
from tomtat.textutil import count_words, nfc, split_sentences
from tomtat.units import assemble_extract, inspect_docx, parse_spec
from tomtat.web import create_app


def _make_docx(path: Path, cell_texts: list[str]) -> Path:
    doc = Document()
    table = doc.add_table(rows=len(cell_texts), cols=1)
    for i, t in enumerate(cell_texts):
        table.rows[i].cells[0].text = t
    doc.save(str(path))
    return path


# ------------------------------------------------------------------ 1. Tách câu & đếm từ

def test_split_sentences_vietnamese_edge_cases():
    text = (
        "1. Tại TP. Hồ Chí Minh, PGS.TS. Nguyễn Văn A. công bố mức tăng trưởng 18,5% "
        "với kinh phí 1.500.000 đồng. "
        "Điều 5. Phạm vi áp dụng cho toàn bộ cơ sở. "
        "Tuy nhiên... nhiều vấn đề còn bỏ ngỏ! "
        "Ông hỏi: “Khi nào hoàn thành?” rồi kết thúc."
    )
    sents = split_sentences(text)
    assert len(sents) == 4
    assert sents[0].startswith("1. Tại TP. Hồ Chí Minh, PGS.TS. Nguyễn Văn A.")
    assert "18,5%" in sents[0]
    assert sents[1] == "Điều 5. Phạm vi áp dụng cho toàn bộ cơ sở."
    assert sents[2] == "Tuy nhiên... nhiều vấn đề còn bỏ ngỏ!"
    assert sents[3] == "Ông hỏi: “Khi nào hoàn thành?” rồi kết thúc."


def test_count_words_ignores_emojis_and_punct():
    txt = "🍀🍀 Xin chào   bạn =))) — đây là 18,5% kết quả! #hashtag"
    # Xin(1) chào(2) bạn(3) đây(4) là(5) 18,5%(6) kết(7) quả!(8) #hashtag(9)
    assert count_words(txt) == 9
    assert count_words("   ---   =)))  🍀 ") == 0


def test_nfc_normalization():
    decomposed = "tiê\u0301ng Viê\u0323t"
    composed = nfc(decomposed)
    assert composed == "tiếng Việt"


# ------------------------------------------------------------------ 2. Nhận diện layout & quy ước M/S

def test_detect_layout_without_label_rows(tmp_path):
    p = _make_docx(tmp_path / "S001.docx", [
        "Câu hỏi truy vấn?",
        "Văn bản gốc duy nhất có nội dung đủ dài.",
        "Gợi ý trích chọn AI.",
        "Gợi ý biên soạn AI.",
    ])
    _, _, rows = load_table(p)
    layout = detect_layout(rows)
    assert "error" not in layout
    assert layout["label_rows_present"] is False
    assert layout["has_query"] is True
    assert layout["doc_idx"] == [1]
    assert layout["ai_extract_idx"] == 2
    assert layout["ai_abstract_idx"] == 3


def test_detect_layout_with_empty_label_rows_and_empty_query(tmp_path):
    p = _make_docx(tmp_path / "M002.docx", [
        "",
        "Văn bản 1.",
        "Văn bản 2.",
        "Gợi ý trích chọn AI.",
        "Gợi ý biên soạn AI.",
        "",
        "",
        "",
    ])
    _, _, rows = load_table(p)
    layout = detect_layout(rows)
    assert layout["label_rows_present"] is True
    assert layout["has_query"] is False
    assert layout["doc_idx"] == [1, 2]
    assert layout["label_idx"] == [5, 6, 7]


def test_filename_convention_check():
    assert check_filename_convention("M54629.docx", 2) is None
    assert check_filename_convention("S10001.docx", 1) is None
    assert check_filename_convention("M54629.docx", 1) is not None
    assert check_filename_convention("S10001.docx", 3) is not None


# ------------------------------------------------------------------ 3. Ghép trích chọn nguyên văn

def test_assemble_extract_preserves_order_and_paragraphs(tmp_path):
    cfg = load_config()
    doc_text = "Câu một đoạn một. Câu hai đoạn một.\nCâu ba đoạn hai. Câu bốn đoạn hai."
    p = _make_docx(tmp_path / "S01.docx", ["", doc_text, "AI ext", "AI abs"])
    data = inspect_docx(p, cfg)
    spec = parse_spec("1: 4, 1-2")
    ext_text, per_doc = assemble_extract(data, spec)
    lines = ext_text.splitlines()
    assert len(lines) == 2
    assert lines[0] == "Câu một đoạn một. Câu hai đoạn một."
    assert lines[1] == "Câu bốn đoạn hai."
    assert per_doc[1] == count_words(ext_text)


# ------------------------------------------------------------------ 4. Kiểm tra cơ học theo phong cách

def test_checks_style_rules_and_numbers(tmp_path):
    cfg = load_config()
    src_sentences = [
        f"Ngày {i} tháng 5, đơn vị công bố kết quả tăng trưởng đạt 18,5% tại khu vực miền Trung."
        for i in range(1, 11)
    ]
    p = _make_docx(tmp_path / "S02.docx", ["", " ".join(src_sentences), "AI ext", "AI abs"])
    data = inspect_docx(p, cfg)
    ext_text, _ = assemble_extract(data, {1: [1, 2, 3]})  # 30%

    # Báo chí có bullet + dấu hai chấm xuống dòng + đổi 18,5 thành 18.5 + số lạ 999
    bad_abstract = (
        "Các nội dung chính:\n"
        "- Đơn vị công bố kết quả tăng trưởng đạt 18.5% tại khu vực miền Trung với 999 đại biểu."
    )
    res = run_checks(data, "báo chí - công luận", ext_text, bad_abstract, cfg)
    codes = {i.code for i in res.issues}
    assert "bullet" in codes
    assert "colon_newline" in codes
    assert "decimal_point" in codes
    assert "number_missing" in codes

    # Sinh hoạt hằng ngày có dòng tiêu đề ngắn
    sh_abs = (
        "Tiêu đề tóm tắt\n"
        "Ngày 1 tháng 5, đơn vị công bố kết quả tăng trưởng đạt 18,5% tại khu vực miền Trung "
        "và tiếp tục duy trì kết quả tăng trưởng đạt 18,5% trong ngày 2 tháng 5."
    )
    res_sh = run_checks(data, "sinh hoạt hằng ngày", ext_text, sh_abs, cfg)
    assert any(i.code == "heading" for i in res_sh.errors)


# ------------------------------------------------------------------ 5. Ghi .docx và bảo vệ input\

def test_fill_docx_creates_yellow_rows_and_protects_input(tmp_path):
    inp_dir = tmp_path / "input"
    out_dir = tmp_path / "output"
    inp_dir.mkdir()
    out_dir.mkdir()

    src = _make_docx(inp_dir / "S03.docx", [
        "Query mẫu",
        "Văn bản gốc mẫu có một số câu.",
        "AI trích chọn",
        "AI biên soạn",
    ])
    # Chặn ghi vào input\
    with pytest.raises(DocxError):
        fill_docx(
            src, inp_dir / "other.docx",
            "khoa học", "Văn bản gốc mẫu.", "Bản biên soạn.",
            protected_dirs=[inp_dir],
        )

    dst = out_dir / "S03.docx"
    info = fill_docx(
        src, dst,
        "khoa học", "Văn bản gốc mẫu có một số câu.", "Bản biên soạn mẫu.",
        protected_dirs=[inp_dir],
    )
    assert info["rows_created"] is True
    assert dst.exists()

    doc2, table2, rows2 = load_table(dst)
    texts2 = [row_text(r) for r in rows2]
    assert len(texts2) == 7
    assert texts2[:4] == ["Query mẫu", "Văn bản gốc mẫu có một số câu.", "AI trích chọn", "AI biên soạn"]
    assert texts2[4] == "khoa học"
    assert texts2[5] == "Văn bản gốc mẫu có một số câu."
    assert texts2[6] == "Bản biên soạn mẫu."
    # Kiểm tra tô màu vàng FFFF00 ở 3 hàng mới
    for idx in (4, 5, 6):
        shd = table2.rows[idx].cells[0]._element.tcPr.findall(qn("w:shd"))
        assert shd and shd[0].get(qn("w:fill")) == "FFFF00"


# ------------------------------------------------------------------ 6. Retry helper & Pipeline E2E + Flask API

def test_transient_error_detection():
    class Fake429Error(Exception):
        status_code = 429

    class Fake400Error(Exception):
        status_code = 400

    assert is_transient(Fake429Error()) is True
    assert is_transient(TimeoutError()) is True
    assert is_transient(Fake400Error()) is False


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
    out = process_file(inp, cfg, client, force=True)
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

    # Kiểm tra tải file ZIP các bài đã xong theo tên ngày hôm nay (VD: 17-11.zip)
    import zipfile
    from datetime import datetime
    r_zip = tc.get("/api/download-zip?stems=M54629")
    assert r_zip.status_code == 200
    expected_zip = f"{datetime.now().strftime('%d-%m')}.zip"
    assert expected_zip in r_zip.headers.get("Content-Disposition", "")
    with zipfile.ZipFile(io_buf := __import__("io").BytesIO(r_zip.data)) as zf:
        assert "M54629.docx" in zf.namelist()
    assert (tmp_path / "output" / expected_zip).exists()

    # Kiểm tra tải lẻ 1 file đã xong
    r_single = tc.get("/api/download-file/M54629")
    assert r_single.status_code == 200

    # Kiểm tra tải file mới lên qua nút "+ Thêm file .docx" (/api/upload) và chạy AI ngay
    import io
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

    # Kiểm tra xóa file lẻ qua /api/delete-files
    r_del = tc.post("/api/delete-files", json={"mode": "selected", "stems": ["M99999"]}).get_json()
    assert r_del["ok"] is True
    assert r_del["count"] == 1
    assert not (tmp_path / "input" / "M99999.docx").exists()


def test_get_api_keys_multi_format(monkeypatch):
    from tomtat.config import get_api_keys

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

