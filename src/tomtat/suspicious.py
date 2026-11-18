"""Kiểm tra và phát hiện văn bản bất thường có dấu hiệu lỗi hoặc bị hủy."""
from __future__ import annotations

from collections import Counter
import re

from .textutil import count_words, nfc, norm

# Từ khóa nhận diện nội dung chuyển tiền / biên lai giao dịch ngân hàng
BANK_KEYWORDS = {
    "chuyển khoản", "chuyển tiền", "số tài khoản", "stk", "ngân hàng", "mã giao dịch",
    "giao dịch thành công", "thụ hưởng", "người thụ hưởng", "tài khoản nguồn", "tài khoản nhận",
    "biên lai", "lệnh chuyển", "biến động số dư", "vietcombank", "techcombank", "vietinbank",
    "bidv", "mbbank", "acb", "tpbank", "vpbank", "agribank", "sacombank", "vnpay", "momo",
    "nội dung chuyển", "nội dung giao dịch", "số dư khả dụng", "ngày hạch toán"
}

VI_STOPWORDS = {
    "và", "của", "các", "có", "cho", "là", "trong", "để", "với", "những", "người", "được",
    "khi", "đã", "sẽ", "ở", "này", "đó", "ra", "vào", "về", "thì", "mà", "như", "theo",
    "tại", "một", "lại", "từ", "phải", "lên", "bị", "hay", "cũng", "đến", "trên", "rất",
    "nhiều", "nhưng", "bởi", "vì", "do", "tự", "nào", "cả", "nữa", "qua", "sau", "trước"
}


def check_suspicious_document(data: dict) -> list[str]:
    """Phát hiện các file có dấu hiệu lỗi bất thường có thể bị hủy:
    1. File chỉ có nội dung chuyển tiền / biên lai giao dịch ngân hàng thực sự (STK, VND, biến động số dư thô)
    2. File không có nội dung chính: danh sách đường link (URL dump), từ điển/bộ thủ, danh bạ/địa chỉ, bảng số liệu thô
    3. File lặp từ bất thường hoặc lặp câu liên tiếp
    4. File cách đoạn không có nghĩa (bị phân mảnh vụn vặt hoặc ký tự rác)
    5. File quá ngắn (< 35 từ) hoặc bảng Word bị lỗi cấu trúc
    """
    reasons: list[str] = []
    docs = data.get("docs") or []
    if not docs:
        return ["File không có nội dung văn bản nào."]

    all_units = [u for d in docs for u in d.get("units") or []]
    all_text = nfc(" ".join(u.get("text", "") for u in all_units)).strip()
    total_w = sum(u.get("words", 0) for u in all_units) or count_words(all_text)
    text_lower = all_text.lower()

    # 1. File quá ngắn / không có nội dung chính
    if total_w < 35:
        reasons.append(f"File quá ngắn (chỉ có {total_w} từ < 35 từ), không đủ nội dung chính để tóm tắt và biên soạn.")

    # 2. File chỉ có nội dung chuyển tiền / biên lai ngân hàng thực sự (tránh nhầm lẫn với bài luận/chia sẻ về nghề trading, tài chính)
    if total_w <= 350:
        has_transfer_term = any(t in text_lower for t in [
            "chuyển khoản", "chuyển tiền", "lệnh chuyển", "ủy nhiệm chi",
            "biên lai chuyển", "biến động số dư", "chuyển thành công"
        ])
        has_account_pattern = bool(re.search(r"(?:stk|số tài khoản|tài khoản nguồn|tài khoản nhận|tài khoản thụ hưởng|tk nhận)\s*[:.]?\s*[\d\w]{6,}", text_lower))
        has_money_pattern = bool(re.search(r"(?:số tiền|biến động|số dư|giao dịch)\s*[:.]?\s*[-+]?\d+[\d.,]*\s*(?:vnd|vnđ|đ|đồng)", text_lower))
        has_ref_code = bool(re.search(r"(?:mã giao dịch|mã gd|số tham chiếu|ft\d{8,}|\bref\b)\s*[:.]?\s*[\w\d]+", text_lower))
        ev_cnt = sum([has_transfer_term, has_account_pattern, has_money_pattern, has_ref_code])
        if (ev_cnt >= 2 and total_w <= 200) or ev_cnt >= 3:
            reasons.append("File chỉ là tin nhắn/biên lai chuyển tiền ngân hàng (chứa STK, số tiền, mã giao dịch thô).")

    # 3. File không có nội dung bài viết: Danh sách link / URL dump (như đề thi dán link Facebook hàng loạt)
    urls = re.findall(r"https?://[^\s]+", all_text)
    if len(urls) >= 5 or (urls and sum(len(u) for u in urls) / max(1, len(all_text)) > 0.15):
        reasons.append(f"File chỉ là danh sách đường link/URL liên kết ({len(urls)} link), không có bài viết chính.")

    # 4. File không có nội dung bài viết: Danh bạ địa chỉ / quán xá liệt kê bằng dấu chấm phẩy dày đặc
    semis = all_text.count(";")
    if semis >= 25 or (semis >= 15 and semis / max(1, total_w / 20) > 1.2):
        reasons.append(f"File chỉ là danh bạ/danh sách địa chỉ quán xá liệt kê thô ({semis} dấu chấm phẩy).")

    # 5. File không có nội dung bài viết: Từ điển tra cứu bộ thủ / ghép từ vựng lặp cấu trúc
    radicals = len(re.findall(r"\bbộ\s+[A-ZĐÀ-Ỹa-zđà-ỹ]+(?:\s*\([^)]*\))?\s*[:–-]", all_text))
    vocab_rhyme = len(re.findall(r"\b(?:có nghĩa là|tạm dịch|nghĩa gốc là|dùng để chỉ|tên gọi|chữ|là con|là cái|là người|thì là|gọi là|dịch là|nghĩa là)\b", text_lower))
    if radicals >= 10:
        reasons.append(f"File là danh mục từ điển/bộ thủ chữ Hán tra cứu ({radicals} mục bộ thủ), không có bài viết chính.")
    elif vocab_rhyme >= 25 and total_w >= 100:
        reasons.append(f"File là danh sách từ vựng/bài ghép từ tra cứu cơ học ({vocab_rhyme} cụm giải nghĩa từ), không có bài viết chính.")

    # 6. File không có nội dung bài viết: Bảng số liệu / lịch trình thô số thứ tự 63 tỉnh thành
    provs = len(re.findall(r"\b\d{1,2}\s+(?:An Giang|Bà Rịa|Bạc Liêu|Bắc Giang|Bắc Kạn|Bắc Ninh|Bến Tre|Bình Dương|Bình Định|Bình Phước|Bình Thuận|Cà Mau|Cần Thơ|Cao Bằng|Đà Nẵng|Đắk Lắk|Đắk Nông|Điện Biên|Đồng Nai|Đồng Tháp|Gia Lai|Hà Giang|Hà Nam|Hà Nội|Hà Tĩnh|Hải Dương|Hải Phòng|Hậu Giang|Hòa Bình|Hưng Yên|Khánh Hòa|Kiên Giang|Kon Tum|Lai Châu|Lâm Đồng|Lạng Sơn|Lào Cai|Long An|Nam Định|Nghệ An|Ninh Bình|Ninh Thuận|Phú Thọ|Phú Yên|Quảng Bình|Quảng Nam|Quảng Ngãi|Quảng Ninh|Quảng Trị|Sóc Trăng|Sơn La|Tây Ninh|Thái Bình|Thái Nguyên|Thanh Hóa|Thừa Thiên Huế|Tiền Giang|TP\.?HCM|Trà Vinh|Tuyên Quang|Vĩnh Long|Vĩnh Phúc|Yên Bái)\b", all_text, re.IGNORECASE))
    if provs >= 15:
        reasons.append(f"File là bảng dữ liệu/lịch trình thô liệt kê theo số thứ tự tỉnh thành ({provs} tỉnh thành), không có bài viết chính.")

    # 7. File lặp từ bất thường
    tokens = re.findall(r"\b\w+\b", text_lower)
    if len(tokens) >= 30:
        content_tokens = [t for t in tokens if len(t) >= 2 and t not in VI_STOPWORDS and not t.isdigit()]
        if content_tokens:
            counts = Counter(content_tokens)
            top_word, top_cnt = counts.most_common(1)[0]
            pct_content = top_cnt / len(content_tokens)
            pct_total = top_cnt / len(tokens)
            if (pct_content > 0.18 and top_cnt >= 8) or (pct_total > 0.14 and top_cnt >= 10):
                reasons.append(f"File lặp từ bất thường: từ “{top_word}” lặp lại {top_cnt} lần (chiếm {pct_total:.1%} văn bản).")

        if len(tokens) >= 60:
            vocab_div = len(set(tokens)) / len(tokens)
            if vocab_div < 0.20:
                reasons.append(f"File có độ đa dạng từ vựng quá thấp ({vocab_div:.1%}), nghi lỗi lặp từ/lặp đoạn.")

    # Lặp lại các câu/unit nguyên văn liên tiếp
    for d in docs:
        units = d.get("units") or []
        consec = 0
        for i in range(1, len(units)):
            u_prev = norm(units[i - 1].get("text", ""))
            u_curr = norm(units[i].get("text", ""))
            if len(u_curr) > 12 and u_curr == u_prev:
                consec += 1
                if consec >= 2:  # 3 câu giống nhau liên tiếp
                    reasons.append("File có các đoạn/câu lặp lại nguyên văn liên tiếp.")
                    break
            else:
                consec = 0

    # 8. File cách đoạn không có nghĩa / phân mảnh vụn vặt / ký tự lạ
    if len(all_units) >= 8:
        avg_w = sum(u.get("words", 0) for u in all_units) / len(all_units)
        if avg_w < 2.5:
            reasons.append(f"File cách đoạn không có nghĩa: văn bản bị cắt vụn quá mức (trung bình {avg_w:.1f} từ/đoạn).")

    if len(all_text) >= 40:
        std_chars = len(re.findall(r"[\w\s.,;:!?\"'()–—\-/]", all_text))
        noise_ratio = (len(all_text) - std_chars) / len(all_text)
        if noise_ratio > 0.15:
            reasons.append(f"File chứa quá nhiều ký tự lạ/ký hiệu bất thường ({noise_ratio:.1%}).")

    return reasons
