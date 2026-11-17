"""Giao diện dòng lệnh (CLI) cho công cụ tomtat."""
from __future__ import annotations

import argparse
import concurrent.futures
import sys
import threading
import webbrowser
from pathlib import Path

from .checks import run_checks
from .config import Config, get_api_key, load_config
from .docx_io import DocxError, fill_docx
from .llm import GeminiClient, MockClient
from .pipeline import STEPS_ORDER, process_file, step_inspect
from .report import collect_rows, write_reports
from .state import (
    STATUS_APPROVED,
    STATUS_EXPORTED,
    STATUS_VI,
    FileWorkspace,
    discover_docx_files,
)
from .textutil import configure as configure_textutil


def _setup_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass


def _build_client(cfg: Config, mock: bool):
    if mock:
        return MockClient(cfg)
    key = get_api_key()
    if not key:
        sys.exit(
            "LỖI: Chưa tìm thấy GEMINI_API_KEY (trong biến môi trường hoặc file .env).\n"
            "Hãy tạo file .env từ .env.example hoặc chạy thử bằng cờ --mock."
        )
    return GeminiClient(cfg, key)


def _print_file_summary(idx: int, total: int, res: dict) -> None:
    st = res["state"]
    if res.get("skipped"):
        print(f"[{idx}/{total}] {st['file']}: BỎ QUA (đã ở trạng thái '{st['status_vi']}'; dùng --force để chạy lại)")
        return
    chk = st.get("checks") or {}
    m = chk.get("metrics") or {}
    lb = (st.get("label") or {}).get("value") or "(chưa có)"
    conf = (st.get("label") or {}).get("confidence") or ""
    ratio_str = f"{m.get('ratio_name', 'B/T')}={m['ratio']:.2f}" if m.get("ratio") is not None else ""
    geval = (st.get("judge") or {}).get("average")
    geval_str = f" | G-Eval={geval}" if geval is not None else ""
    print(
        f"[{idx}/{total}] {st['file']} -> {st['status_vi'].upper()} | "
        f"Nhãn: {lb} ({conf}) | "
        f"Từ: gốc {m.get('total_words', 0)}, trích {m.get('extract_words', 0)} "
        f"(Compress={m.get('compress', 0):.2f}), biên soạn {m.get('abstract_words', 0)} ({ratio_str})"
        f"{geval_str} | Lỗi: {chk.get('n_errors', 0)}, Cảnh báo: {chk.get('n_warnings', 0)}"
    )
    for fl in st.get("flags") or []:
        print(f"    ! Cờ lưu ý: {fl}")
    for iss in (chk.get("issues") or [])[:5]:
        if iss.get("level") in ("error", "warning"):
            print(f"    - [{iss.get('level_vi')}] {iss.get('message')}")


def cmd_process(args: argparse.Namespace, cfg: Config) -> int:
    target = Path(args.target) if args.target else cfg.input_dir
    if not target.is_absolute():
        target = (Path.cwd() / target).resolve()
    files = discover_docx_files(target)
    if not files:
        print(f"Không tìm thấy file .docx nào trong: {target}")
        return 1

    sc = cfg.get("sentence_split", {})
    configure_textutil(sc.get("abbreviations"), sc.get("heading_words"))

    # Nếu chỉ chạy bước inspect thì không cần khởi tạo Gemini client
    if args.only == "inspect":
        client = MockClient(cfg)
    else:
        client = _build_client(cfg, args.mock)

    workers = max(1, int(args.workers or cfg.get("workers", 2)))
    print(
        f"Bắt đầu xử lý {len(files)} file (luồng song song={workers}, "
        f"model={'mock' if args.mock else cfg['gemini']['model']}"
        f"{f', chỉ bước={args.only}' if args.only else ''})..."
    )

    results = []
    if workers == 1 or len(files) == 1:
        for i, fpath in enumerate(files, start=1):
            r = process_file(
                fpath, cfg, client, force=args.force, only_step=args.only,
                ignore_mismatch=args.ignore_mismatch,
            )
            _print_file_summary(i, len(files), r)
            results.append(r)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            fut_map = {
                pool.submit(
                    process_file, fpath, cfg, client,
                    force=args.force, only_step=args.only,
                    ignore_mismatch=args.ignore_mismatch,
                ): fpath
                for fpath in files
            }
            for i, fut in enumerate(concurrent.futures.as_completed(fut_map), start=1):
                r = fut.result()
                _print_file_summary(i, len(files), r)
                results.append(r)

    xlsx_path, csv_path = write_reports(cfg)
    counts: dict[str, int] = {}
    for r in results:
        st_name = r["state"]["status_vi"]
        counts[st_name] = counts.get(st_name, 0) + 1
    summary = ", ".join(f"{k}: {v}" for k, v in counts.items())
    print(f"\nHoàn tất. Tổng kết trạng thái: {summary}")
    print(f"Đã cập nhật báo cáo: {xlsx_path} (và {csv_path})")
    return 0


def _free_port_windows(port: int) -> None:
    """Tự động đóng các tiến trình cũ đang giữ cổng `port` trước khi khởi động server mới."""
    import os
    import subprocess

    my_pid = os.getpid()
    try:
        out = subprocess.check_output(
            ["netstat", "-ano", "-p", "TCP"],
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
        pids: set[int] = set()
        target_suffix = f":{port}"
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[1].endswith(target_suffix) and parts[3].upper() == "LISTENING":
                try:
                    pid = int(parts[4])
                    if pid > 0 and pid != my_pid:
                        pids.add(pid)
                except ValueError:
                    pass
        for pid in pids:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
    except Exception:  # noqa: BLE001
        pass


def cmd_review(args: argparse.Namespace, cfg: Config) -> int:
    from .web import create_app

    host = "127.0.0.1"
    port = int(args.port or cfg.get("review", {}).get("port", 8765))
    _free_port_windows(port)
    app = create_app(cfg, mock=args.mock)
    url = f"http://{host}:{port}"
    print(f"Đang mở giao diện duyệt tại {url} (nhấn Ctrl+C để dừng)...")
    if not args.no_browser and cfg.get("review", {}).get("open_browser", True):
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    app.run(host=host, port=port, debug=False, use_reloader=False)
    return 0


def cmd_export(args: argparse.Namespace, cfg: Config) -> int:
    files = discover_docx_files(cfg.input_dir)
    known = {p.stem: p for p in files}
    if cfg.work_dir.exists():
        for d in sorted(cfg.work_dir.iterdir()):
            if d.is_dir() and (d / "state.json").exists() and d.name not in known:
                st_tmp = FileWorkspace(cfg.work_dir, d / f"{d.name}.docx").load_state()
                src = Path(st_tmp.get("source_path") or (cfg.input_dir / f"{d.name}.docx"))
                if src.exists():
                    known[d.name] = src

    if args.targets:
        selected = []
        for t in args.targets:
            stem = Path(t).stem
            if stem in known:
                selected.append(known[stem])
            elif Path(t).exists():
                selected.append(Path(t).resolve())
            else:
                print(f"CẢNH BÁO: Không tìm thấy file '{t}'.")
    else:
        selected = list(known.values())

    allowed_statuses = {STATUS_APPROVED, STATUS_EXPORTED}
    if args.include_drafts:
        from .state import STATUS_DRAFT
        allowed_statuses.add(STATUS_DRAFT)

    exported = 0
    skipped = 0
    failed = 0
    for docx_path in selected:
        ws = FileWorkspace(cfg.work_dir, docx_path)
        st = ws.load_state()
        if not args.targets and st["status"] not in allowed_statuses:
            skipped += 1
            continue
        units_data = ws.load_units() or step_inspect(ws, cfg, st)
        style = (st.get("label") or {}).get("value", "")
        extract = (st.get("extract") or {}).get("text", "")
        abstract = (st.get("abstract") or {}).get("text", "")
        chk = run_checks(units_data, style, extract, abstract, cfg)
        if not chk.ok:
            print(f"[CHẶN] {docx_path.name}: còn {len(chk.errors)} LỖI cơ học, không thể xuất.")
            failed += 1
            continue
        out_path = cfg.output_dir / docx_path.name
        try:
            info = fill_docx(
                docx_path,
                out_path,
                style,
                extract,
                abstract,
                protected_dirs=[cfg.input_dir],
                allow_overwrite_content=True,
            )
            st["status"] = STATUS_EXPORTED
            st["output_path"] = str(out_path)
            ws.save_state(st)
            exported += 1
            print(
                f"[ĐÃ XUẤT] {info['output']} (nhãn='{style}', trích={info['extract_words']}w, "
                f"biên soạn={info['abstract_words']}w, giữ nguyên {info['rows_kept']} hàng trên)"
            )
        except DocxError as exc:
            print(f"[LỖI] {docx_path.name}: {exc}")
            failed += 1

    xlsx_path, _ = write_reports(cfg)
    print(f"\nKết quả xuất: thành công {exported}, bỏ qua {skipped}, lỗi/chặn {failed}. Báo cáo: {xlsx_path}")
    return 0 if failed == 0 else 1


def cmd_status(args: argparse.Namespace, cfg: Config) -> int:
    rows = collect_rows(cfg)
    if not rows:
        print(f"Chưa có file nào trong '{cfg.input_dir}' hoặc '{cfg.work_dir}'.")
        return 0

    header = (
        f"{'TÊN FILE':<18} {'LOẠI':<5} {'TRẠNG THÁI':<15} {'NHÃN PHONG CÁCH':<22} "
        f"{'GỐC':>5} {'TRÍCH':>5} {'BSOẠN':>5} {'COMP':>6} {'TỈ LỆ':<11} {'G-EVAL':>6} {'LỖI/CB':>7}"
    )
    print(header)
    print("-" * len(header))
    for r in rows:
        comp_s = f"{r['compress']:.2f}" if isinstance(r["compress"], (int, float)) and r["compress"] else "—"
        ge_s = f"{r['geval']:.2f}" if isinstance(r["geval"], (int, float)) else "—"
        err_w = f"{r['n_errors']}/{r['n_warnings']}"
        print(
            f"{r['file'][:18]:<18} {r['kind'] + '(' + str(r['n_docs']) + ')':<5} "
            f"{r['status_vi']:<15} {(r['label'] or '—')[:22]:<22} "
            f"{r['total_words']:>5} {r['extract_words']:>5} {r['abstract_words']:>5} "
            f"{comp_s:>6} {(r['ratio_display'] or '—'):<11} {ge_s:>6} {err_w:>7}"
        )
        if r["warnings"]:
            print(f"   ↳ Cảnh báo: {r['warnings'][:140]}{'...' if len(r['warnings']) > 140 else ''}")

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["status_vi"]] = counts.get(r["status_vi"], 0) + 1
    print("-" * len(header))
    print("Tổng hợp:", ", ".join(f"{k}: {v}" for k, v in counts.items()))
    if args.report:
        xlsx_path, csv_path = write_reports(cfg)
        print(f"Đã ghi báo cáo: {xlsx_path} và {csv_path}")
    return 0


def cmd_report(args: argparse.Namespace, cfg: Config) -> int:
    out = Path(args.out) if args.out else None
    xlsx_path, csv_path = write_reports(cfg, out_xlsx=out)
    print(f"Đã xuất báo cáo tổng hợp:\n  - Excel: {xlsx_path}\n  - CSV  : {csv_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tomtat",
        description="Công cụ tự động hóa hoàn thiện file .docx dự án Tóm tắt - Biên soạn tiếng Việt.",
    )
    p.add_argument("--config", help="Đường dẫn tới file config.yaml (tùy chọn).")
    sub = p.add_subparsers(dest="cmd", required=True)

    # process
    sp_proc = sub.add_parser("process", help="Xử lý một file .docx hoặc cả thư mục (mặc định: input\\).")
    sp_proc.add_argument("target", nargs="?", default=None, help="File .docx hoặc thư mục cần xử lý.")
    sp_proc.add_argument("--workers", type=int, default=None, help="Số luồng xử lý song song.")
    sp_proc.add_argument("--force", action="store_true", help="Chạy lại kể cả những file đã có nháp/đã duyệt.")
    sp_proc.add_argument("--only", choices=STEPS_ORDER, default=None, help="Chỉ chạy một bước trong pipeline.")
    sp_proc.add_argument("--mock", action="store_true", help="Chạy bằng client Gemini giả lập (không tốn API).")
    sp_proc.add_argument("--ignore-mismatch", action="store_true", help="Tiếp tục xử lý kể cả khi tên file M/S lệch số văn bản.")

    # review
    sp_rev = sub.add_parser("review", help="Mở giao diện web local trên 127.0.0.1 để duyệt và chỉnh sửa.")
    sp_rev.add_argument("--port", type=int, default=None, help="Cổng lắng nghe (mặc định trong config.yaml: 8765).")
    sp_rev.add_argument("--mock", action="store_true", help="Bật sẵn chế độ giả lập trên giao diện.")
    sp_rev.add_argument("--no-browser", action="store_true", help="Không tự mở trình duyệt.")

    # export
    sp_exp = sub.add_parser("export", help="Ghi các file đã duyệt ra thư mục output\\.")
    sp_exp.add_argument("targets", nargs="*", help="Tên file cụ thể cần xuất (bỏ trống để xuất theo trạng thái).")
    sp_exp.add_argument("--all-approved", action="store_true", default=True, help="Xuất tất cả các file đã duyệt.")
    sp_exp.add_argument("--include-drafts", action="store_true", help="Xuất cả những file đang ở trạng thái 'đã có nháp' sạch lỗi.")

    # status
    sp_st = sub.add_parser("status", help="In bảng trạng thái các file và cảnh báo.")
    sp_st.add_argument("--report", action="store_true", help="Cập nhật luôn file báo cáo XLSX/CSV.")

    # report
    sp_rep = sub.add_parser("report", help="Xuất báo cáo tổng hợp XLSX và CSV.")
    sp_rep.add_argument("-o", "--out", default=None, help="Đường dẫn file .xlsx đầu ra.")

    return p


def main(argv: list[str] | None = None) -> int:
    _setup_utf8_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    dispatch = {
        "process": cmd_process,
        "review": cmd_review,
        "export": cmd_export,
        "status": cmd_status,
        "report": cmd_report,
    }
    return dispatch[args.cmd](args, cfg)


if __name__ == "__main__":
    raise SystemExit(main())
