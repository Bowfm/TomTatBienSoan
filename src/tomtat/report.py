"""Xuất báo cáo tổng hợp ra XLSX (và CSV UTF-8 BOM) từ dữ liệu các file."""
from __future__ import annotations

import csv
from pathlib import Path
import re
import threading

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from .docx_io import filename_kind
from .state import STATUS_VI, FileWorkspace, discover_docx_files

HEADERS = [
    "Tên file",
    "Loại (M/S)",
    "Số văn bản",
    "Nhãn phong cách",
    "Độ chắc chắn nhãn",
    "Số từ gốc",
    "Số từ trích chọn",
    "Số từ biên soạn",
    "Compress (T/G)",
    "Tỉ lệ B/T",
    "Edit",
    "Độ bám từ ngữ",
    "Điểm G-Eval",
    "Số lỗi",
    "Số cảnh báo",
    "Cảnh báo / Ghi chú",
    "Trạng thái",
]


def infer_step_models_from_logs(ws: FileWorkspace, st: dict) -> dict[str, str]:
    """Trả về tên model đã chạy của từng bước {'label': ..., 'extract': ..., 'abstract': ..., 'judge': ...}."""
    out: dict[str, str] = {}
    for step in ("label", "extract", "abstract", "judge"):
        sec = st.get(step)
        if isinstance(sec, dict) and sec.get("model_used"):
            out[step] = str(sec["model_used"])
    if ws.logs_dir.exists() and len(out) < 4:
        import json
        for lp in sorted(ws.logs_dir.glob("*.json"), reverse=True)[:30]:
            try:
                d = json.loads(lp.read_text(encoding="utf-8"))
                step = d.get("step", "")
                if step in ("label", "extract", "abstract", "judge") and step not in out:
                    if d.get("parsed_ok") and d.get("model"):
                        out[step] = str(d["model"])
            except Exception:  # noqa: BLE001
                pass
    return out


def _infer_model_from_logs(ws: FileWorkspace, st: dict) -> str:
    step_map = infer_step_models_from_logs(ws, st)
    for k in ("abstract", "extract", "label"):
        if step_map.get(k):
            return step_map[k]
    return st.get("model_used") or ""


def collect_rows(cfg: dict, files: list[Path] | None = None, reverse: bool = True) -> list[dict]:
    if files is None:
        files = discover_docx_files(cfg.input_dir)
    # Bổ sung cả những thư mục đã có trong work\ dù file không nằm trong input\
    known = {p.stem: p for p in files}
    if cfg.work_dir.exists():
        for d in sorted(cfg.work_dir.iterdir()):
            if d.is_dir() and (d / "state.json").exists() and d.name not in known:
                st_tmp = FileWorkspace(cfg.work_dir, d / f"{d.name}.docx").load_state()
                src = Path(st_tmp.get("source_path") or (d / f"{d.name}.docx"))
                known[d.name] = src
    rows = []
    def _natural_key(s: str) -> list[int | str]:
        return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(s))]

    for stem in sorted(known, key=_natural_key, reverse=reverse):
        docx_path = known[stem]
        ws = FileWorkspace(cfg.work_dir, docx_path)
        st = ws.load_state()
        units = ws.load_units() or {}
        chk = st.get("checks") or {}
        metrics = chk.get("metrics") or {}
        issues = chk.get("issues") or []
        judge = st.get("judge") or {}

        warn_msgs = list(st.get("flags") or [])
        for iss in issues:
            if iss.get("level") in ("error", "warning"):
                warn_msgs.append(f"[{iss.get('level_vi', '')}] {iss.get('message', '')}")
        ratio_str = ""
        if metrics.get("ratio") is not None:
            ratio_str = f"{metrics.get('ratio_name', 'B/T')} = {metrics['ratio']:.3f}"

        model_used = _infer_model_from_logs(ws, st)

        # Kiểm tra file bất thường có thể bị hủy
        susp_reasons = metrics.get("suspicious_reasons")
        if susp_reasons is None and units:
            from .suspicious import check_suspicious_document
            susp_reasons = check_suspicious_document(units)
        is_susp = bool(metrics.get("is_suspicious") or susp_reasons)

        rows.append({
            "file": st.get("file") or docx_path.name,
            "kind": units.get("kind") or filename_kind(docx_path.name) or "?",
            "n_docs": len(units.get("docs") or []),
            "label": (st.get("label") or {}).get("value", ""),
            "confidence": (st.get("label") or {}).get("confidence", ""),
            "model_used": model_used,
            "total_words": metrics.get("total_words") or (units.get("budget") or {}).get("total_words", 0),
            "extract_words": metrics.get("extract_words") or (st.get("extract") or {}).get("words", 0),
            "abstract_words": metrics.get("abstract_words") or (st.get("abstract") or {}).get("words", 0),
            "compress": metrics.get("compress") or (st.get("extract") or {}).get("compress", 0.0),
            "ratio_display": ratio_str,
            "edit": metrics.get("edit", ""),
            "coverage": metrics.get("coverage", ""),
            "geval": judge.get("average", ""),
            "n_errors": chk.get("n_errors", 0),
            "n_warnings": chk.get("n_warnings", 0),
            "warnings": " | ".join(warn_msgs),
            "is_suspicious": is_susp,
            "suspicious_reasons": susp_reasons or [],
            "status": st.get("status"),
            "status_vi": STATUS_VI.get(st.get("status", ""), st.get("status", "")),
            "updated_at": st.get("updated_at", ""),
            "current_step": st.get("current_step") or "",
            "current_version": int(st.get("current_version") or 0),
        })
    return rows


_REPORT_LOCK = threading.Lock()


def write_reports(cfg: dict, out_xlsx: Path | None = None, files: list[Path] | None = None) -> tuple[Path, Path]:
    with _REPORT_LOCK:
        xlsx_path = Path(out_xlsx) if out_xlsx else cfg.path("report")
        xlsx_path.parent.mkdir(parents=True, exist_ok=True)
        csv_path = xlsx_path.with_suffix(".csv")
        rows = collect_rows(cfg, files)

        wb = Workbook()
        ws = wb.active
        ws.title = "Tổng hợp"
        ws.append(HEADERS)
        header_fill = PatternFill("solid", fgColor="1F4E78")
        header_font = Font(color="FFFFFF", bold=True)
        for col_idx in range(1, len(HEADERS) + 1):
            c = ws.cell(row=1, column=col_idx)
            c.fill = header_fill
            c.font = header_font
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

        for r in rows:
            ws.append([
                r["file"],
                r["kind"],
                r["n_docs"],
                r["label"],
                r["confidence"],
                r["total_words"],
                r["extract_words"],
                r["abstract_words"],
                r["compress"],
                r["ratio_display"],
                r["edit"],
                r["coverage"],
                r["geval"],
                r["n_errors"],
                r["n_warnings"],
                r["warnings"],
                r["status_vi"],
            ])
        widths = [20, 10, 12, 22, 16, 12, 15, 15, 14, 18, 10, 14, 12, 10, 12, 55, 16]
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w
        wb.save(str(xlsx_path))

        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(HEADERS)
            for r in rows:
                writer.writerow([
                    r["file"], r["kind"], r["n_docs"], r["label"], r["confidence"],
                    r["total_words"], r["extract_words"], r["abstract_words"], r["compress"],
                    r["ratio_display"], r["edit"], r["coverage"], r["geval"],
                    r["n_errors"], r["n_warnings"], r["warnings"], r["status_vi"],
                ])
        return xlsx_path, csv_path
