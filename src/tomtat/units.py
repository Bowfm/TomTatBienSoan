"""Chia văn bản gốc thành đơn vị câu/đề mục và ghép bản trích chọn từ id.

Tái cấu trúc từ skill_ref/.../scripts/common.py (build_units), inspect_docx.py, build_extract.py.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # chỉ dùng cho type hints, tránh import vòng lúc chạy
    from .models import DocumentBudget, UnitItem, UnitsData

import math
import re
from pathlib import Path

from .docx_io import DocxError, check_filename_convention, detect_layout, filename_kind, load_table, row_text
from .textutil import count_words, split_sentences


def build_units(paras: list[str]) -> list[UnitItem]:
    """Chia các đoạn của một văn bản thành đơn vị đánh số từ 1."""
    units, uid, para_no = [], 0, 0
    for p in paras:
        if not p.strip():
            continue
        para_no += 1
        for s in split_sentences(p):
            uid += 1
            units.append({"id": uid, "para": para_no, "text": s, "words": count_words(s)})
    return units


def compute_budget(total: int, cfg: dict) -> DocumentBudget:
    g, e = cfg["grading"], cfg["extract"]
    return {
        "total_words": total,
        "extract_min": math.ceil(total * g["compress_min"]),
        "extract_max": math.floor(total * g["compress_max"]),
        "extract_target_low": round(total * e["target_low"]),
        "extract_target_high": round(total * e["target_high"]),
        "extract_target_mid": round(total * e["target"]),
        "target_low_pct": int(round(e["target_low"] * 100)),
        "target_high_pct": int(round(e["target_high"] * 100)),
        "target_mid_pct": int(round(e["target"] * 100)),
    }


def abstract_targets(n_extract: int, cfg: dict) -> dict:
    """Khoảng số từ mục tiêu cho bản biên soạn, tính từ số từ bản trích chọn."""
    a = cfg["abstract"]
    lim = a["ratio_limit"]
    short_max = math.floor(n_extract * lim)
    long_min = math.ceil(n_extract / lim) if n_extract else 0
    allow_longer = n_extract < a["allow_longer_below_words"]
    return {
        "direction": "longer" if allow_longer else "shorter",
        "allow_longer": allow_longer,
        "short_max": short_max,
        "short_low": round(n_extract * a["short_target_low"]),
        "short_high": min(short_max, round(n_extract * a["short_target_high"])),
        "long_min": long_min,
        "long_target": max(long_min, round(n_extract * a["long_target_factor"])),
    }


def inspect_docx(path: str | Path, cfg: dict) -> UnitsData:
    """Đọc file, nhận diện bố cục, chia đơn vị. Trả về dữ liệu 'units' của file."""
    path = Path(path)
    _, _, rows = load_table(path)
    layout = detect_layout(rows)
    if "error" in layout:
        raise DocxError(layout["error"])
    query = row_text(rows[0]) if layout["has_query"] else ""
    docs = []
    for k, ri in enumerate(layout["doc_idx"], start=1):
        units = build_units(rows[ri])
        docs.append({"n": k, "row": ri, "words": sum(u["words"] for u in units), "units": units})
    total = sum(d["words"] for d in docs)
    mismatch = None
    if cfg.get("layout", {}).get("check_filename_convention", True):
        mismatch = check_filename_convention(path.name, len(docs))
    existing_labels = [row_text(rows[i]) for i in layout["label_idx"]]
    return {
        "file": path.name,
        "kind": filename_kind(path.name),
        "query": query,
        "layout": layout,
        "docs": docs,
        "ai_extract": row_text(rows[layout["ai_extract_idx"]]),
        "ai_abstract": row_text(rows[layout["ai_abstract_idx"]]),
        "existing_labels": existing_labels,
        "budget": compute_budget(total, cfg),
        "filename_mismatch": mismatch,
    }


def normalize_selections(data: dict, selections) -> tuple[dict[int, list[int]], list[str]]:
    """Chuẩn hóa lựa chọn {doc_n: [ids]} hoặc [{doc, ids}], trả về (lựa chọn hợp lệ, lỗi)."""
    if isinstance(selections, list):
        sel: dict[int, set[int]] = {}
        for item in selections:
            sel.setdefault(int(item["doc"]), set()).update(int(i) for i in item.get("ids", []))
    else:
        sel = {int(k): set(int(i) for i in v) for k, v in (selections or {}).items()}
    docs = {d["n"]: d for d in data["docs"]}
    errors, out = [], {}
    for n, ids in sel.items():
        if n not in docs:
            errors.append(f"Không có văn bản số {n} (chỉ có {sorted(docs)}).")
            continue
        valid = {u["id"] for u in docs[n]["units"]}
        bad = sorted(i for i in ids if i not in valid)
        if bad:
            errors.append(f"Văn bản {n} không có đơn vị số {bad} (chỉ có 1..{len(valid)}).")
        out[n] = sorted(i for i in ids if i in valid)
    return out, errors


def assemble_extract(data: dict, selections) -> tuple[str, dict]:
    """Ghép nguyên văn các đơn vị đã chọn theo đúng thứ tự gốc.

    Các câu cùng đoạn được nối trên một dòng; đoạn khác/văn bản khác xuống dòng.
    """
    sel, _ = normalize_selections(data, selections)
    out_lines: list[str] = []
    per_doc: dict[int, int] = {}
    for d in data["docs"]:
        ids = sel.get(d["n"]) or []
        if not ids:
            continue
        units = {u["id"]: u for u in d["units"]}
        cur_para, cur, words = None, [], 0
        for i in ids:
            u = units[i]
            words += u["words"]
            if cur_para is not None and u["para"] != cur_para:
                out_lines.append(" ".join(cur))
                cur = []
            cur_para = u["para"]
            cur.append(u["text"])
        if cur:
            out_lines.append(" ".join(cur))
        per_doc[d["n"]] = words
    return "\n".join(out_lines), per_doc


def parse_spec(spec: str) -> dict[int, list[int]]:
    """'1: 1-3, 6\\n2: 4' -> {1: [1,2,3,6], 2: [4]} (định dạng spec của skill)."""
    out: dict[int, set[int]] = {}
    for ln in spec.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        m = re.fullmatch(r"(\d+)\s*:\s*(.+)", ln)
        if not m:
            raise ValueError(f"Dòng spec sai định dạng: '{ln}'")
        ids = out.setdefault(int(m.group(1)), set())
        for part in re.split(r"[,\s]+", m.group(2).strip()):
            if not part:
                continue
            r = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
            if r:
                a, b = sorted((int(r.group(1)), int(r.group(2))))
                ids.update(range(a, b + 1))
            elif part.isdigit():
                ids.add(int(part))
            else:
                raise ValueError(f"Không hiểu '{part}' trong spec.")
    return {k: sorted(v) for k, v in out.items()}


def units_listing(data: dict, with_words: bool = True) -> str:
    """Văn bản gốc dạng '[id] (n từ) câu', dùng trong prompt."""
    lines = []
    for d in data["docs"]:
        lines.append(f"=== VĂN BẢN {d['n']} ({d['words']} từ, {len(d['units'])} đơn vị) ===")
        last = None
        for u in d["units"]:
            if last is not None and u["para"] != last:
                lines.append("")
            last = u["para"]
            w = f" ({u['words']} từ)" if with_words else ""
            lines.append(f"[{u['id']}]{w} {u['text']}")
        lines.append("")
    return "\n".join(lines).strip()


def source_text(data: dict, doc_n: int | None = None) -> str:
    """Văn bản gốc (đoạn cách nhau bằng xuống dòng)."""
    parts = []
    for d in data["docs"]:
        if doc_n is not None and d["n"] != doc_n:
            continue
        paras: dict[int, list[str]] = {}
        for u in d["units"]:
            paras.setdefault(u["para"], []).append(u["text"])
        body = "\n".join(" ".join(v) for v in paras.values())
        parts.append(body if doc_n is not None else f"=== VĂN BẢN {d['n']} ===\n{body}")
    return "\n\n".join(parts)
