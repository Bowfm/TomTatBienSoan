"""Ho tro kiem tra an toan file stage va tao commit message cho day_cap_nhat.bat."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FORBIDDEN_EXACT_NAMES = frozenset({
    "api_keys.txt",
    ".env",
    "config.local.yaml",
    "quota_exhausted.json",
    ".startup_update_cache.json",
    ".updater_manifest.json",
    "baocao.xlsx",
})

ALLOWED_WORK_ENTRIES = frozenset({
    "work/learned_memory.json",
    "work/.gitkeep",
    "input/.gitkeep",
    "output/.gitkeep",
})

FORBIDDEN_PREFIXES = (
    "input/",
    "output/",
    "work/",
    ".venv/",
    ".pytest_cache/",
)

FORBIDDEN_SUFFIXES = (".zip", ".pyc", ".pyo")

BUG_HEADER_ADD_RE = re.compile(r"^\+###\s+(BUG-\d+)\s*:\s*(.+)$")
BUG_HEADER_DEL_RE = re.compile(r"^-###\s+(BUG-\d+)\s*:\s*(.+)$")


def ascii_fold(text: str) -> str:
    """Chuyen chuoi tieng Viet co dau thanh ASCII sach tren 1 dong."""
    replaced = str(text or "").replace("đ", "d").replace("Đ", "D")
    norm = unicodedata.normalize("NFKD", replaced)
    stripped = "".join(ch for ch in norm if unicodedata.category(ch) != "Mn")
    ascii_only = stripped.encode("ascii", errors="ignore").decode("ascii")
    return " ".join(ascii_only.split())


def is_forbidden_staged_path(rel_path: str) -> str | None:
    """Tra ve ly do neu duong dan dang stage la file bi mat hoac du lieu ca nhan."""
    norm = rel_path.replace("\\", "/").lstrip("/")
    while norm.startswith("./"):
        norm = norm[2:]
    if not norm:
        return None
    low = norm.lower()
    basename = low.rsplit("/", 1)[-1]

    if basename in FORBIDDEN_EXACT_NAMES:
        return "file bi mat / du lieu cuc bo"
    if basename.startswith("~$"):
        return "file tam cua Microsoft Word"
    if low in ALLOWED_WORK_ENTRIES:
        return None
    if low.startswith(FORBIDDEN_PREFIXES) or "/__pycache__/" in f"/{low}":
        return "thu muc du lieu ca nhan / moi truong / cache tam"
    if low.endswith(FORBIDDEN_SUFFIXES):
        return "file nen / bytecode tam"
    return None


def _run_git(root: Path, args: list[str]) -> str:
    res = subprocess.run(
        ["git", "-c", "core.quotepath=false", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(root),
        timeout=15,
    )
    if res.returncode != 0:
        return ""
    return res.stdout


def get_staged_files(root: Path) -> list[str]:
    """Lay danh sach file da stage trong Git index."""
    out = _run_git(root, ["diff", "--cached", "--name-only"])
    return [ln.strip().replace("\\", "/") for ln in out.splitlines() if ln.strip()]


def check_staged(root: Path) -> tuple[bool, list[tuple[str, str]]]:
    """Kiem tra toan bo file dang stage, tra ve (an_toan, danh_sach_vi_pham)."""
    bad: list[tuple[str, str]] = []
    for path in get_staged_files(root):
        reason = is_forbidden_staged_path(path)
        if reason:
            bad.append((path, reason))
    return len(bad) == 0, bad


def extract_added_bugs(root: Path) -> list[tuple[str, str]]:
    """Tim cac muc ### BUG-XXX moi duoc them trong docs/KNOWN_BUGS.md dang stage."""
    diff_txt = _run_git(root, ["diff", "--cached", "-U0", "--", "docs/KNOWN_BUGS.md"])
    if not diff_txt:
        return []
    removed_ids: set[str] = set()
    added: list[tuple[str, str]] = []
    for line in diff_txt.splitlines():
        if line.startswith("---"):
            continue
        m_del = BUG_HEADER_DEL_RE.match(line)
        if m_del:
            removed_ids.add(m_del.group(1).strip())
            continue
        if line.startswith("+++"):
            continue
        m_add = BUG_HEADER_ADD_RE.match(line)
        if m_add:
            bug_id = m_add.group(1).strip()
            title = ascii_fold(m_add.group(2).strip().replace("`", ""))
            added.append((bug_id, title))
    return [(bid, t) for bid, t in added if bid not in removed_ids]


def _read_learned_count(root: Path) -> int | None:
    mem_path = root / "work" / "learned_memory.json"
    if not mem_path.exists():
        return None
    try:
        data = json.loads(mem_path.read_text(encoding="utf-8-sig"))
        val = data.get("total_approved_learned")
        return int(val) if val is not None else None
    except Exception:
        return None


def _summarize_areas(staged: list[str]) -> list[str]:
    rules: list[tuple[callable, str]] = [
        (lambda p: p == "config.yaml" or p.endswith("/config.py"), "cau hinh"),
        (lambda p: p.startswith("prompts/") or p.endswith("/prompts.py"), "prompts"),
        (lambda p: p.startswith("src/tomtat/llm/"), "LLM"),
        (lambda p: p.startswith("src/tomtat/web/"), "giao dien web"),
        (lambda p: p.startswith("src/tomtat/checks/"), "kiem tra"),
        (lambda p: p.endswith("/updater.py") or p.endswith(".bat"), "cap nhat/script"),
        (lambda p: p.startswith("src/tomtat/"), "xu ly"),
        (lambda p: p.startswith("tests/"), "tests"),
        (lambda p: p.startswith("docs/"), "tai lieu"),
        (lambda p: p == "work/learned_memory.json", "bo nho hoc tap"),
    ]
    areas: list[str] = []
    for path in staged:
        low = path.lower()
        for pred, label in rules:
            if pred(low):
                if label not in areas:
                    areas.append(label)
                break
    return areas


def _truncate(text: str, limit: int = 115) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def build_commit_message(root: Path, *, now: datetime | None = None) -> str:
    """Tao thong diep commit ngan gon, ro rang cho nguoi dung xem tren banner cap nhat."""
    staged = get_staged_files(root)
    ts = (now or datetime.now()).strftime("%Y-%m-%d %H:%M")
    if not staged:
        return f"Cap nhat cong cu TomTatBienSoan ({ts})"

    new_bugs = extract_added_bugs(root)
    has_memory = "work/learned_memory.json" in staged
    learned_count = _read_learned_count(root) if has_memory else None

    if new_bugs:
        if len(new_bugs) == 1:
            bug_id, title = new_bugs[0]
            msg = f"Cap nhat ({bug_id}): {title}"
            if has_memory and len(staged) > 1 and len(msg) <= 95:
                msg += " (+ learned_memory)"
            return _truncate(msg, 118)
        ids = ", ".join(bid for bid, _ in new_bugs)
        last_title = new_bugs[-1][1]
        return _truncate(f"Cap nhat ({ids}): {last_title}", 118)

    if staged == ["work/learned_memory.json"]:
        count_txt = f" ({learned_count} bai)" if learned_count is not None else ""
        return f"Cap nhat bo nho hoc tap{count_txt} ({ts})"

    areas = _summarize_areas(staged)
    if areas:
        area_txt = ", ".join(areas[:4])
        if len(areas) > 4:
            area_txt += ", ..."
        return _truncate(f"Cap nhat cong cu ({area_txt}) - {ts}", 118)

    return f"Cap nhat cong cu TomTatBienSoan ({ts})"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Helper cho day_cap_nhat.bat")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_chk = sub.add_parser("check-staged", help="Kiem tra khong lo file nhay cam")
    p_chk.add_argument("--root", default=str(ROOT))

    p_msg = sub.add_parser("commit-message", help="Sinh commit message tu thay doi dang stage")
    p_msg.add_argument("--root", default=str(ROOT))
    p_msg.add_argument("--output", required=True)

    args = parser.parse_args(argv)
    root = Path(args.root).resolve()

    if args.cmd == "check-staged":
        ok, bad = check_staged(root)
        if not ok:
            print("[CANH BAO BAO MAT] Phat hien file bi cam trong danh sach stage:")
            for path, reason in bad:
                print(f" - {path} ({reason})")
            return 1
        return 0

    if args.cmd == "commit-message":
        msg = build_commit_message(root)
        out_path = Path(args.output)
        out_path.write_text(msg + "\n", encoding="utf-8")
        print(msg)
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
