"""Giao diện dòng lệnh (CLI) cho công cụ tomtat."""
from __future__ import annotations

import argparse
import concurrent.futures
import sys
import threading
import webbrowser
from pathlib import Path

from .config import Config, clamp_workers, get_pool_scaling
from .pipeline import STEPS_ORDER
from .services import FileService, make_client
from .textutil import configure as configure_textutil


def _setup_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass


def _build_client(cfg: Config, mock: bool):
    """Tạo client qua cùng đường với Web (services.make_client), chỉ khác cách báo lỗi."""
    try:
        return make_client(cfg, force_mock=mock)
    except RuntimeError as exc:
        sys.exit(f"LỖI: {exc}")


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
    stale = st.get("stale_steps") or []
    if stale:
        reason = f"{st['stale_reason']}. " if st.get("stale_reason") else ""
        print(f"    ! Cần chỉnh sửa lại các phần khác: {reason}{', '.join(stale)}")
    for iss in (chk.get("issues") or [])[:5]:
        if iss.get("level") in ("error", "warning"):
            print(f"    - [{iss.get('level_vi')}] {iss.get('message')}")


def cmd_process(args: argparse.Namespace, cfg: Config) -> int:
    service = FileService(cfg)
    target = Path(args.target) if args.target else cfg.input_dir
    if not target.is_absolute():
        target = (Path.cwd() / target).resolve()
    files = service.discover_files(target)
    if not files:
        print(f"Không tìm thấy file .docx nào trong: {target}")
        return 1

    sc = cfg.get("sentence_split", {})
    configure_textutil(sc.get("abbreviations"), sc.get("heading_words"))

    # Nếu chỉ chạy bước inspect thì không cần API key → dùng mock client
    client = _build_client(cfg, mock=args.mock or args.only == "inspect")

    scaling = get_pool_scaling(cfg)
    raw_w = args.workers if args.workers is not None else cfg.get("workers")
    is_int_w = isinstance(raw_w, int) and not isinstance(raw_w, bool)
    workers = clamp_workers(raw_w if is_int_w else None, cfg)
    if is_int_w and raw_w > workers and scaling.get("max_workers"):
        print(f"[!] Yêu cầu {raw_w} luồng vượt trần max_workers={scaling['max_workers']} trong config.yaml -> dùng {workers} luồng.")
    print(
        f"Bắt đầu xử lý {len(files)} file (luồng song song={workers}, pool={scaling['key_count']} keys, "
        f"model={'mock' if args.mock else cfg['gemini']['model']}"
        f"{f', chỉ bước={args.only}' if args.only else ''})..."
    )

    results = []
    if workers == 1 or len(files) == 1:
        for i, fpath in enumerate(files, start=1):
            r = service.process(
                fpath, client, force=args.force, only_step=args.only,
                ignore_mismatch=args.ignore_mismatch,
            )
            _print_file_summary(i, len(files), r)
            results.append(r)
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            fut_map = {
                pool.submit(
                    service.process, fpath, client,
                    force=args.force, only_step=args.only,
                    ignore_mismatch=args.ignore_mismatch,
                ): fpath
                for fpath in files
            }
            for i, fut in enumerate(concurrent.futures.as_completed(fut_map), start=1):
                r = fut.result()
                _print_file_summary(i, len(files), r)
                results.append(r)

    xlsx_path, csv_path = service.write_reports()
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
    from .web.app import get_startup_update_info

    host = "127.0.0.1"
    port = int(args.port or cfg.get("review", {}).get("port", 8765))
    _free_port_windows(port)
    app = create_app(cfg, mock=args.mock)
    threading.Thread(
        target=get_startup_update_info,
        args=(app,),
        daemon=True,
        name="startup-update-check",
    ).start()
    url = f"http://{host}:{port}"
    print(f"Đang mở giao diện duyệt tại {url} (nhấn Ctrl+C để dừng)...")
    if not args.no_browser and cfg.get("review", {}).get("open_browser", True):
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)  # threaded: cần cho SSE
    return 0


def cmd_export(args: argparse.Namespace, cfg: Config) -> int:
    service = FileService(cfg)
    result = service.export_batch(args.targets, include_drafts=args.include_drafts)
    for d in result["details"]:
        if d["status"] == "exported":
            info = d["info"]
            print(
                f"[ĐÃ XUẤT] {info['output']} (nhãn='{info.get('style', '')}', trích={info['extract_words']}w, "
                f"biên soạn={info['abstract_words']}w, giữ nguyên {info['rows_kept']} hàng trên)"
            )
        elif d["status"] == "blocked":
            print(f"[CHẶN] {d['file']}: còn {d['errors']} LỖI cơ học, không thể xuất.")
        elif d["status"] == "error":
            print(f"[LỖI] {d['file']}: {d['error']}")

    xlsx_path, _ = service.write_reports()
    print(
        f"\nKết quả xuất: thành công {result['exported']}, "
        f"bỏ qua {result['skipped']}, lỗi/chặn {result['failed']}. Báo cáo: {xlsx_path}"
    )
    return 0 if result["failed"] == 0 else 1


def cmd_status(args: argparse.Namespace, cfg: Config) -> int:
    service = FileService(cfg)
    rows = service.get_status_rows()
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
        xlsx_path, csv_path = service.write_reports()
        print(f"Đã ghi báo cáo: {xlsx_path} và {csv_path}")
    return 0


def cmd_report(args: argparse.Namespace, cfg: Config) -> int:
    service = FileService(cfg)
    out = Path(args.out) if args.out else None
    xlsx_path, csv_path = service.write_reports(out_xlsx=out)
    print(f"Đã xuất báo cáo tổng hợp:\n  - Excel: {xlsx_path}\n  - CSV  : {csv_path}")
    return 0


def cmd_versions(args: argparse.Namespace, cfg: Config) -> int:
    service = FileService(cfg)
    stem = Path(args.target).stem
    try:
        if args.save:
            detail = service.get_file_detail(stem)
            st = detail["state"]
            res = service.save_draft(
                stem=stem,
                style=(st.get("label") or {}).get("value", ""),
                extract=(st.get("extract") or {}).get("text", ""),
                abstract=(st.get("abstract") or {}).get("text", ""),
                selections=(st.get("extract") or {}).get("selections"),
                note=args.note or "Lưu thủ công (CLI)",
                custom_edited=bool((st.get("extract") or {}).get("custom_edited", False)),
                create_version=True,
            )
            print(f"Đã lưu phiên bản v{res['version']:03d} cho {stem}.docx.")
            return 0
        if args.rollback is not None:
            ver = None if args.rollback == "previous" else int(args.rollback)
            res = service.restore_draft(stem=stem, version=ver)
            print(
                f"Đã rollback {stem}.docx về v{res['restored_from']:03d} "
                f"(ghi thành phiên bản mới v{res['version']:03d})."
            )
            return 0
        info = service.get_file_versions(stem)
    except (FileNotFoundError, ValueError) as exc:
        print(f"LỖI: {exc}", file=sys.stderr)
        return 1

    versions = info["versions"]
    if not versions:
        print(f"{stem}.docx: chưa có phiên bản nào được lưu.")
        return 0
    print(
        f"Lịch sử phiên bản của {stem}.docx (hiện tại: v{info['current_version']:03d}):"
    )
    for v in versions:
        ver_num = int(v.get("version") or 0)
        mark = "*" if ver_num == info["current_version"] and not info["has_unsaved_changes"] else " "
        lb = (v.get("label") or {}).get("value") or "—"
        ew = (v.get("extract") or {}).get("words", 0)
        aw = (v.get("abstract") or {}).get("words", 0)
        chk = v.get("checks_summary") or {}
        ok_str = "Đạt" if chk.get("ok") else f"Lỗi:{chk.get('n_errors', 0)}"
        note_str = f" — {v['note']}" if v.get("note") else ""
        print(
            f" {mark} v{ver_num:03d} | {v.get('saved_at', '')} | {v.get('source', '')} | "
            f"{lb} | trích {ew}w, bsoạn {aw}w | {ok_str}{note_str}"
        )
    return 0


def cmd_rpd(args: argparse.Namespace, cfg: Config) -> int:
    from .llm import get_key_rpd_details
    from .services.batch_service import unlock_rpd_quota

    if args.unlock:
        res = unlock_rpd_quota(cfg, model=args.model or None)
        print(f"Đã mở khóa {res['unlocked']} mục RPD/cooldown.")

    details = get_key_rpd_details(cfg)
    print(
        f"Chu kỳ RPD: {details['cycle_date']} (reset: {details['next_reset_local']}) | "
        f"Tổng keys: {details['key_count']}"
    )
    for m_info in details["models"]:
        qps_note = f", QPS={m_info.get('qps_429_used', 0)}" if m_info.get("qps_429_used") else ""
        print(
            f" - {m_info['model']}: tổng {m_info['used']}/{m_info['limit']} "
            f"(OK={m_info['ok_used']}, 503/Timeout/Hủy={m_info['transient_used']}{qps_note}) | "
            f"còn {m_info['available_keys']}/{details['key_count']} key"
        )
    models_to_show = [args.model] if args.model else [m["model"] for m in details["models"]]
    for row in details["keys"]:
        parts = []
        for m in models_to_show:
            cell = row["models"].get(m)
            if not cell:
                continue
            if args.exhausted_only and not cell["exhausted"] and not cell["strikes_429"]:
                continue
            qps_cell = f", QPS={cell['qps_429_count']}, gửi={cell['total_sent_with_qps']}" if cell.get("qps_429_count") else ""
            parts.append(
                f"{m}=[{cell['status_vi']}; OK={cell['ok_used']}, 503/TO={cell['transient_used']}{qps_cell}, "
                f"tổng={cell['used']}/{cell['rpd_limit']}]"
            )
        if parts:
            print(f"  {row['key_label']}: " + " | ".join(parts))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tomtat",
        description="Bộ công cụ tự động hóa hoàn thiện file .docx cho dự án Tóm tắt - Biên soạn.",
    )
    p.add_argument("--config", default=None, help="Đường dẫn file config.yaml")
    sub = p.add_subparsers(dest="command", required=True)

    # process
    p_proc = sub.add_parser("process", help="Chạy pipeline tự động cho các file trong input/")
    p_proc.add_argument("target", nargs="?", default=None, help="File .docx hoặc thư mục cần xử lý")
    p_proc.add_argument("--only", choices=STEPS_ORDER, default=None, help="Chỉ chạy một bước duy nhất")
    p_proc.add_argument("--force", action="store_true", help="Chạy lại ngay cả khi đã hoàn thành")
    p_proc.add_argument("--mock", action="store_true", help="Chạy ở chế độ giả lập (không gọi Gemini)")
    p_proc.add_argument("--workers", type=int, default=None, help="Số luồng xử lý song song")
    p_proc.add_argument("--ignore-mismatch", action="store_true", help="Bỏ qua cảnh báo lệch cấu trúc")
    p_proc.set_defaults(func=cmd_process)

    # review
    p_rev = sub.add_parser("review", help="Mở giao diện web để duyệt và chỉnh sửa nháp")
    p_rev.add_argument("--port", type=int, default=None, help="Cổng chạy server (mặc định 8765)")
    p_rev.add_argument("--no-browser", action="store_true", help="Không tự động mở trình duyệt")
    p_rev.add_argument("--mock", action="store_true", help="Chạy review ở chế độ giả lập")
    p_rev.set_defaults(func=cmd_review)

    # export
    p_exp = sub.add_parser("export", help="Ghi kết quả đã duyệt ra thư mục output/")
    p_exp.add_argument("targets", nargs="*", default=None, help="Danh sách tên file cần xuất")
    p_exp.add_argument("--include-drafts", action="store_true", help="Xuất cả các file mới có nháp (chưa duyệt)")
    p_exp.set_defaults(func=cmd_export)

    # status
    p_stat = sub.add_parser("status", help="Xem bảng tổng quan trạng thái các file")
    p_stat.add_argument("--report", action="store_true", help="Cập nhật lại file baocao.xlsx")
    p_stat.set_defaults(func=cmd_status)

    # report
    p_rep = sub.add_parser("report", help="Xuất lại file báo cáo baocao.xlsx và baocao.csv")
    p_rep.add_argument("--out", default=None, help="Đường dẫn file Excel đích")
    p_rep.set_defaults(func=cmd_report)

    # versions
    p_ver = sub.add_parser("versions", help="Xem, lưu và rollback phiên bản của một file")
    p_ver.add_argument("target", help="Tên file (.docx) hoặc stem cần xem/rollback")
    p_ver.add_argument("--save", action="store_true", help="Lưu trạng thái hiện tại thành version mới")
    p_ver.add_argument("--note", default="", help="Ghi chú khi lưu version mới")
    p_ver.add_argument(
        "--rollback",
        nargs="?",
        const="previous",
        default=None,
        help="Rollback về version trước (mặc định) hoặc số version chỉ định",
    )
    p_ver.set_defaults(func=cmd_versions)

    # rpd
    p_rpd = sub.add_parser("rpd", help="Xem trạng thái còn/hết RPD của từng model theo từng API key")
    p_rpd.add_argument("--model", default="", help="Lọc theo tên model cụ thể")
    p_rpd.add_argument("--exhausted-only", action="store_true", help="Chỉ hiện các key đang bị khóa hoặc có cảnh báo 429")
    p_rpd.add_argument("--unlock", action="store_true", help="Mở khóa trạng thái hết RPD/cooldown hiện tại")
    p_rpd.set_defaults(func=cmd_rpd)

    return p


def main(argv: list[str] | None = None) -> int:
    _setup_utf8_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    from .config import load_config
    cfg = load_config(args.config)
    return args.func(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
