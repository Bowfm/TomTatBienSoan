"""Bộ nhớ tự học (Adaptive Few-Shot & Correction Memory) từ các bài người dùng đã duyệt.

File bộ nhớ được lưu tại `work/learned_memory.json` và KHÔNG bị xóa khi dọn batch cuối ngày,
giúp công cụ càng chạy nhiều ngày càng bám sát chuẩn gán nhãn, trích chọn và biên soạn.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from . import STYLES
from .state import read_json, write_json_atomic
from .textutil import count_words, nfc

MAX_LABEL_CORRECTIONS = 15       # Tối đa 15 ca sửa nhãn khó để đưa vào prompt
MAX_STYLE_EXAMPLES_PER_STYLE = 2  # Tối đa 2 bài mẫu chuẩn nhất cho mỗi phong cách (5 x 2 = 10 mẫu)


def memory_path(work_dir: Path) -> Path:
    return Path(work_dir) / "learned_memory.json"


def load_memory(work_dir: Path) -> dict:
    p = memory_path(work_dir)
    data = read_json(p)
    if not data:
        return {
            "total_approved_learned": 0,
            "updated_at": None,
            "label_corrections": [],
            "style_examples": {s: [] for s in STYLES},
        }
    data.setdefault("total_approved_learned", 0)
    data.setdefault("label_corrections", [])
    se = data.setdefault("style_examples", {})
    for s in STYLES:
        se.setdefault(s, [])
    return data


def record_approved_sample(work_dir: Path, units_data: dict, st: dict,
                           final_style: str, final_extract: str, final_abstract: str) -> dict:
    """Ghi nhận kinh nghiệm mỗi khi người dùng bấm 'Duyệt & Ghi .docx'."""
    mem = load_memory(work_dir)
    stem = st.get("stem") or units_data.get("file", "")
    ai_label = (st.get("label") or {}).get("model_label") or (st.get("label") or {}).get("value") or ""
    ai_conf = (st.get("label") or {}).get("confidence") or ""

    # Lấy đoạn trích ngắn đại diện của văn bản gốc (tối đa ~120 từ đầu)
    snippet_parts = []
    for d in (units_data.get("docs") or [])[:2]:
        u_texts = [u["text"] for u in (d.get("units") or [])[:3]]
        snippet_parts.append(f"[VB{d['n']}] " + " ".join(u_texts))
    snippet = " ".join(snippet_parts)[:550]

    # 1. Nếu người dùng đã sửa nhãn của AI (hoặc ca đó AI không chắc chắn 'cao' mà người dùng đã chốt)
    if final_style in STYLES and ((ai_label and ai_label != final_style) or ai_conf in ("thap", "trung_binh")):
        corrs = [c for c in mem["label_corrections"] if c.get("stem") != stem]
        corrs.insert(0, {
            "stem": stem,
            "ai_guessed": ai_label or "(không rõ)",
            "human_approved": final_style,
            "snippet": snippet,
            "learned_at": datetime.now().isoformat(timespec="seconds"),
        })
        mem["label_corrections"] = corrs[:MAX_LABEL_CORRECTIONS]

    # 2. Lưu bài mẫu chuẩn (Few-Shot) theo đúng phong cách chức năng đã duyệt
    if final_style in STYLES and final_abstract.strip() and final_extract.strip():
        total_w = units_data["budget"]["total_words"]
        n_ext = count_words(final_extract)
        n_abs = count_words(final_abstract)
        compress = round(n_ext / total_w, 3) if total_w else 0.3
        ratio_bt = round(n_abs / n_ext, 3) if n_ext else 0.6

        # Ghi nhận xem người dùng có chỉnh sửa bản biên soạn so với bản đầu tiên của AI không
        edits_note = ""
        ai_orig_abs = (st.get("abstract") or {}).get("text", "").strip()
        if ai_orig_abs and nfc(ai_orig_abs) != nfc(final_abstract.strip()):
            diff_w = n_abs - count_words(ai_orig_abs)
            if abs(diff_w) >= 5:
                edits_note = (
                    f"Người duyệt đã {'rút ngắn bớt ' + str(-diff_w) if diff_w < 0 else 'bổ sung thêm ' + str(diff_w)} từ "
                    f"để đạt tỉ lệ B/T = {ratio_bt:.0%}."
                )
            else:
                edits_note = "Người duyệt đã tinh chỉnh câu chữ và dấu câu chuẩn xác hơn."

        bucket = [ex for ex in mem["style_examples"].get(final_style, []) if ex.get("stem") != stem]
        bucket.insert(0, {
            "stem": stem,
            "style": final_style,
            "n_docs": len(units_data.get("docs") or []),
            "compress": compress,
            "ratio_bt": ratio_bt,
            "extract_sample": final_extract.strip()[:700],
            "abstract_sample": final_abstract.strip()[:900],
            "edits_note": edits_note,
            "learned_at": datetime.now().isoformat(timespec="seconds"),
        })
        mem["style_examples"][final_style] = bucket[:MAX_STYLE_EXAMPLES_PER_STYLE]

    mem["total_approved_learned"] = int(mem.get("total_approved_learned", 0)) + 1
    mem["updated_at"] = datetime.now().isoformat(timespec="seconds")
    write_json_atomic(memory_path(work_dir), mem)
    return mem


def format_label_memory(work_dir: Path) -> str:
    """Chuỗi kinh nghiệm phân loại nhãn từ các bài người dùng đã sửa/duyệt."""
    mem = load_memory(work_dir)
    lines: list[str] = []
    for c in (mem.get("label_corrections") or [])[:6]:
        lines.append(
            f"- Bài `{c['stem']}` (AI từng đoán `{c['ai_guessed']}` -> Người duyệt chốt chuẩn là **`{c['human_approved']}`**): "
            f"“{c['snippet'][:260]}...”"
        )
    if not lines:
        return ""
    return (
        "## KINH NGHIỆM RÚT RA TỪ CÁC BÀI NGƯỜI DUYỆT ĐÃ CHỐT TRƯỚC ĐÂY (hãy học theo chuẩn này):\n"
        + "\n".join(lines)
    )


def format_style_memory(work_dir: Path, style: str) -> str:
    """Chuỗi bài mẫu chuẩn (Few-shot) của đúng phong cách đang biên soạn."""
    mem = load_memory(work_dir)
    examples = (mem.get("style_examples") or {}).get(style) or []
    if not examples:
        return ""
    blocks: list[str] = [
        f"## BÀI MẪU CHUẨN ĐÃ ĐƯỢC NGƯỜI DUYỆT PHÊ DUYỆT CHO PHONG CÁCH `{style.upper()}`\n"
        "Hãy học theo cách hành văn, cách ngắt đoạn, độ súc tích và tỷ lệ độ dài của (các) bài mẫu đã đạt chuẩn dưới đây:"
    ]
    for i, ex in enumerate(examples[:2], start=1):
        note_line = f"\n- Lưu ý khi người duyệt chỉnh sửa: {ex['edits_note']}" if ex.get("edits_note") else ""
        blocks.append(
            f"### Mẫu chuẩn #{i} (Bài `{ex['stem']}`, Compress={ex['compress']:.0%}, B/T={ex['ratio_bt']:.0%}){note_line}\n"
            f"**Trích chọn mẫu (trích đoạn):**\n\"\"\"\n{ex['extract_sample']}\n\"\"\"\n"
            f"**Bản biên soạn chuẩn đã duyệt:**\n\"\"\"\n{ex['abstract_sample']}\n\"\"\""
        )
    return "\n\n".join(blocks)
