"""Kiểm thử tầng kiểm tra cơ học: quy tắc phong cách, số liệu, file bất thường, đa văn bản."""
from __future__ import annotations

from tomtat.checks import check_suspicious_document, clean_multi_doc_abstract, run_checks
from tomtat.config import load_config
from tomtat.units import assemble_extract, inspect_docx


def test_checks_style_rules_and_numbers(tmp_path, make_docx):
    cfg = load_config()
    src_sentences = [
        f"Ngày {i} tháng 5, đơn vị công bố kết quả tăng trưởng đạt 18,5% tại khu vực miền Trung."
        for i in range(1, 11)
    ]
    p = make_docx(tmp_path / "S02.docx", ["", " ".join(src_sentences), "AI ext", "AI abs"])
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


def test_check_suspicious_document():
    # 1. File quá ngắn (< 35 từ)
    data_short = {
        "docs": [{"units": [{"text": "Bài viết này quá ngắn không có nội dung.", "words": 9}]}]
    }
    reasons = check_suspicious_document(data_short)
    assert any("quá ngắn" in r for r in reasons)

    # 2. File biên lai giao dịch chuyển tiền
    data_bank = {
        "docs": [{
            "units": [
                {"text": "Ngân hàng Vietcombank thông báo chuyển khoản thành công.", "words": 8},
                {"text": "Số tài khoản: 0123456789.", "words": 4},
                {"text": "Số tiền: 500.000 VND.", "words": 4},
                {"text": "Mã giao dịch: FT12345678.", "words": 4},
                {"text": "Số dư biến động tăng.", "words": 5},
                {"text": "Cảm ơn quý khách đã sử dụng dịch vụ.", "words": 8},
            ]
        }]
    }
    reasons = check_suspicious_document(data_bank)
    assert any("chuyển tiền" in r for r in reasons)

    # 3. File lặp từ bất thường
    data_loop = {
        "docs": [{
            "units": [
                {"text": "khóa học " * 20, "words": 40},
                {"text": "khóa học " * 15, "words": 30},
            ]
        }]
    }
    reasons = check_suspicious_document(data_loop)
    assert any("lặp từ bất thường" in r or "đa dạng từ vựng quá thấp" in r for r in reasons)

    # 4. File cách đoạn vô nghĩa (cắt vụn < 2.5 từ/đoạn)
    data_fragmented = {
        "docs": [{
            "units": [{"text": f"Đoạn {i}", "words": 2} for i in range(1, 10)]
        }]
    }
    reasons = check_suspicious_document(data_fragmented)
    assert any("cắt vụn quá mức" in r for r in reasons)

    # 5. File URL dump (danh sách link facebook)
    data_urls = {
        "docs": [{
            "units": [
                {"text": f"Đề thi {i} môn Văn https://facebook.com/post/{i}", "words": 7}
                for i in range(1, 8)
            ]
        }]
    }
    assert any("URL liên kết" in r for r in check_suspicious_document(data_urls))

    # 6. File danh bạ địa chỉ quán xá (nhiều dấu chấm phẩy)
    data_addresses = {
        "docs": [{
            "units": [
                {"text": "Quán A-123 Đường B; Quán C-456 Đường D; " * 15, "words": 60}
            ]
        }]
    }
    assert any("địa chỉ quán xá" in r for r in check_suspicious_document(data_addresses))

    # 7. File trading tài chính bình thường -> KHÔNG ĐƯỢC BÁO LỖI
    data_trading = {
        "docs": [{
            "units": [
                {"text": "Hầu hết các trader không giao dịch với áp lực kiểu như thế, phần lớn các trader huyền thoại là nhà quản lý tiền tệ hoặc làm việc cho các ngân hàng hoặc quỹ đầu tư.", "words": 34},
                {"text": "Bạn cần phải có một lịch sử giao dịch đủ dài để biết liệu bạn có thể giao dịch thành công hay không trước khi thực hiện các bước nhảy vọt tiếp theo.", "words": 31},
                {"text": "Hãy chuẩn bị tâm lý thật vững vàng và kỷ luật quản lý vốn khi bước vào thị trường tài chính đầy thử thách.", "words": 23},
            ]
        }]
    }
    assert check_suspicious_document(data_trading) == []

    # 8. File bình thường hợp lệ -> không có cảnh báo
    data_valid = {
        "docs": [{
            "units": [
                {"text": "Đây là bài viết phân tích về tình hình phát triển kinh tế xã hội của đất nước trong năm qua.", "words": 19},
                {"text": "Các chuyên gia đã đưa ra nhiều khuyến nghị quan trọng nhằm thúc đẩy tăng trưởng bền vững.", "words": 16},
                {"text": "Đồng thời cần chú trọng nâng cao chất lượng nguồn nhân lực và phát triển khoa học công nghệ.", "words": 17},
            ]
        }]
    }
    assert check_suspicious_document(data_valid) == []


def test_multi_doc_abstract_cleaning_and_blank_line():
    cfg = load_config()

    raw_abs = (
        "Cuối năm 2011, Tony tranh thủ học nhiều chứng chỉ nghề vào cuối tuần để nâng cao kỹ năng.\n"
        "Trong chia sẻ khác, ngành dịch vụ được xem là ngành nghề dạy người ta biết nhẫn nhịn và bền bỉ."
    )
    cleaned = clean_multi_doc_abstract(raw_abs, n_docs=2)
    assert "\n\n" in cleaned
    assert "Trong chia sẻ khác" not in cleaned
    assert "Ngành dịch vụ được xem là" in cleaned

    data_multi = {
        "docs": [
            {"n": 1, "units": [{"id": 1, "text": "Văn bản một có nội dung này.", "words": 7}]},
            {"n": 2, "units": [{"id": 2, "text": "Văn bản hai có nội dung kia.", "words": 7}]},
        ],
        "budget": {"total_words": 100, "extract_min": 10, "extract_max": 50, "extract_target_mid": 30},
    }
    bad_abs = (
        "Văn bản một có nội dung này.\n"
        "Trong chia sẻ khác, văn bản hai có nội dung kia."
    )
    chk = run_checks(data_multi, "báo chí - công luận", "Văn bản một có nội dung này.", bad_abs, cfg)
    codes = [i.code for i in chk.issues]
    assert "multi_doc_transition" in codes
    assert "multi_doc_no_blank_line" in codes

    good_abs = (
        "Văn bản một có nội dung này.\n\n"
        "Văn bản hai có nội dung kia."
    )
    chk_good = run_checks(data_multi, "báo chí - công luận", "Văn bản một có nội dung này.", good_abs, cfg)
    codes_good = [i.code for i in chk_good.issues]
    assert "multi_doc_transition" not in codes_good
    assert "multi_doc_no_blank_line" not in codes_good
