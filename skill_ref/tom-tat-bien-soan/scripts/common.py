"""Hàm dùng chung cho các script của skill tom-tat-bien-soan."""
import re
import sys
import unicodedata

from docx import Document

STYLES = [
    "sinh hoạt hằng ngày",
    "hành chính - công vụ",
    "khoa học",
    "báo chí - công luận",
    "chính luận",
]

# Từ viết tắt thường gặp: dấu chấm sau chúng không kết thúc câu.
ABBREV = {"tp", "ts", "pgs", "gs", "ths", "bs", "ks", "cn", "tt", "tx",
          "q", "p", "đ", "nxb", "vd", "tr", "st"}

COMPRESS_MIN, COMPRESS_MAX = 0.10, 0.50
COMPRESS_TARGET_LOW, COMPRESS_TARGET_HIGH = 0.20, 0.40
RATIO_LIMIT = 0.70  # quy tắc 70% giữa bản biên soạn và bản trích chọn


def nfc(text):
    return unicodedata.normalize("NFC", text or "")


def norm(text):
    """Chuẩn hóa Unicode NFC và gộp khoảng trắng."""
    return re.sub(r"\s+", " ", nfc(text)).strip()


def count_words(text):
    """Đếm các đơn vị tách bằng khoảng trắng có chứa ít nhất một chữ/số."""
    return sum(1 for t in nfc(text).split() if re.search(r"\w", t))


def split_sentences(text):
    """Tách một đoạn thành các câu. Gần đúng, đủ dùng cho tiếng Việt."""
    text = norm(text)
    if not text:
        return []
    out, start = [], 0
    for m in re.finditer(r'([.!?…]+["”’\')\]]*)\s+', text):
        nxt = text[m.end():m.end() + 1]
        if not nxt or not (nxt.isupper() or nxt.isdigit() or nxt in '"“‘(['):
            continue
        before = text[start:m.start()].split()
        prior = before[-1].strip('"“”‘’()[]').lower() if before else ""
        if m.group(1) == ".":
            if prior in ABBREV:
                continue
            if len(prior) == 1 and prior.isalpha() and text[m.start() - 1].isupper():
                continue  # chữ cái viết tắt đứng riêng, ví dụ "Nguyễn Văn A."
            if prior.isdigit() and len(prior) <= 2 and m.start() - start < 6:
                continue  # số thứ tự đầu dòng "1. ..."
        out.append(text[start:m.end()].strip())
        start = m.end()
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return out


def paragraphs_of(cell):
    """Danh sách đoạn (chuỗi) của một ô, tách cả xuống dòng mềm."""
    paras = []
    for p in cell.paragraphs:
        for line in p.text.split("\n"):
            paras.append(line)
    return paras


def load_table(path):
    doc = Document(path)
    if not doc.tables:
        sys.exit("LỖI: file không có bảng nào.")
    table = doc.tables[0]
    rows = [paragraphs_of(r.cells[0]) for r in table.rows]
    return doc, table, rows


def row_text(paras):
    return "\n".join(p for p in paras if p.strip()).strip()


def _blank_or_placeholder(t):
    t = t.strip()
    return (not t) or bool(re.fullmatch(r"<[^<>]{0,80}>", t))


def detect_layout(rows):
    """Nhận diện vai trò các hàng. rows: list[list[str]] (đoạn trong từng ô)."""
    texts = [row_text(r) for r in rows]
    n = len(texts)
    notes = []
    if n < 4:
        return {"error": f"Bảng chỉ có {n} hàng, ít hơn mức tối thiểu 4 "
                         "(query, 1 văn bản, 2 hàng gợi ý của AI)."}
    label_rows = 0
    if n >= 7:
        tail = texts[-3:]
        if texts[-3].strip().lower() in STYLES:
            label_rows = 3
        elif _blank_or_placeholder(texts[-1]):
            label_rows = 3
        elif all(_blank_or_placeholder(t) for t in tail):
            label_rows = 3
    elif n >= 4 and _blank_or_placeholder(texts[-1]):
        notes.append("Hàng cuối trống nhưng bảng quá ngắn để có đủ 3 hàng nhãn; "
                     "kiểm tra lại cấu trúc.")
    ai_extract_idx = n - label_rows - 2
    ai_abstract_idx = n - label_rows - 1
    doc_idx = list(range(1, ai_extract_idx))
    if not doc_idx:
        return {"error": "Không xác định được hàng văn bản gốc nào."}
    query = texts[0]
    has_query = not _blank_or_placeholder(query)
    return {
        "n_rows": n,
        "label_rows_present": label_rows == 3,
        "query_idx": 0,
        "has_query": has_query,
        "doc_idx": doc_idx,
        "ai_extract_idx": ai_extract_idx,
        "ai_abstract_idx": ai_abstract_idx,
        "label_idx": [n - 3, n - 2, n - 1] if label_rows == 3 else [],
        "notes": notes,
    }


def build_units(paras):
    """Chia các đoạn của một văn bản thành đơn vị (câu/đề mục) đánh số từ 1."""
    units, uid = [], 0
    para_no = 0
    for p in paras:
        if not p.strip():
            continue
        para_no += 1
        for s in split_sentences(p):
            uid += 1
            units.append({"id": uid, "para": para_no, "text": s,
                          "words": count_words(s)})
    return units
