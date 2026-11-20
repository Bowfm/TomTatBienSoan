"""Fast static & invariant guard for TomTatBienSoan (<1s).

Runs automatically via the global Regression Guard Stop hook whenever code is modified,
and can be invoked directly with:
    .venv\\Scripts\\python.exe .agents\\verify_guard.py
"""
from __future__ import annotations

import py_compile
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

IMPORT_RE = re.compile(
    r"import\s+\{([^}]+)\}\s+from\s+['\"](\.[^'\"]+)['\"]\s*;",
    re.MULTILINE,
)
EXPORT_RE = re.compile(
    r"export\s+(?:async\s+)?(?:function|const|let|var|class)\s+([A-Za-z_$][A-Za-z0-9_$]*)",
)


def check_python_compile() -> list[str]:
    errors: list[str] = []
    for base in (ROOT / "src", ROOT / "tests", ROOT / ".agents"):
        if not base.exists():
            continue
        for py_file in sorted(base.rglob("*.py")):
            if "__pycache__" in py_file.parts:
                continue
            try:
                py_compile.compile(str(py_file), doraise=True)
            except py_compile.PyCompileError as exc:
                errors.append(f"Python syntax error in {py_file.relative_to(ROOT)}: {exc.msg}")
    return errors


def check_config() -> list[str]:
    errors: list[str] = []
    sys.path.insert(0, str(ROOT / "src"))
    try:
        from tomtat.config import load_config

        cfg = load_config(root=ROOT)
        errs = cfg.validate(raise_on_error=False)
        errors.extend(f"config.yaml: {e}" for e in errs)

        g = cfg.get("gemini") or {}
        chain = [g.get("model")] + list(g.get("fallback_models") or [])
        banned_prefixes = ("gemini-2.5", "gemini-3.7", "gemini-3.5-pro")
        for m in chain:
            if not m:
                continue
            for pref in banned_prefixes:
                if str(m).startswith(pref):
                    errors.append(f"config.yaml contains deprecated/invalid model '{m}'")
    except Exception as exc:
        errors.append(f"Failed to validate config.yaml: {exc}")
    return errors


def check_frontend_js() -> list[str]:
    errors: list[str] = []
    static_dir = ROOT / "src" / "tomtat" / "web" / "static"
    if not static_dir.exists():
        return errors

    js_files = sorted(static_dir.rglob("*.js"))
    node_exe = shutil.which("node")
    if node_exe:
        for js_file in js_files:
            res = subprocess.run(
                [node_exe, "--check", str(js_file)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            if res.returncode != 0:
                msg = (res.stderr or res.stdout or "").strip()
                errors.append(f"JS syntax error in {js_file.relative_to(ROOT)}: {msg}")

    exports_by_file: dict[Path, set[str]] = {}
    for js_file in js_files:
        text = js_file.read_text(encoding="utf-8")
        exports_by_file[js_file.resolve()] = set(EXPORT_RE.findall(text))

    for js_file in js_files:
        text = js_file.read_text(encoding="utf-8")
        for names_raw, rel_target in IMPORT_RE.findall(text):
            target = (js_file.parent / rel_target).resolve()
            if not target.exists():
                errors.append(
                    f"Broken JS import in {js_file.relative_to(ROOT)}: '{rel_target}' does not exist"
                )
                continue
            target_exports = exports_by_file.get(target, set())
            for item in names_raw.split(","):
                item = item.strip()
                if not item:
                    continue
                orig_name = item.split(" as ")[0].strip()
                if orig_name not in target_exports:
                    errors.append(
                        f"Missing JS export '{orig_name}' imported by {js_file.relative_to(ROOT)} from '{rel_target}'"
                    )

    index_html = ROOT / "src" / "tomtat" / "web" / "templates" / "index.html"
    if index_html.exists():
        html = index_html.read_text(encoding="utf-8")
        for match in re.findall(r'(?:src|href)="/static/([^"?]+)', html):
            if not (static_dir / match).exists():
                errors.append(f"index.html references missing static file '/static/{match}'")

    return errors


def check_guard_files() -> list[str]:
    errors: list[str] = []
    for rel in ("docs/KNOWN_BUGS.md", "tests/test_regressions.py", "AGENTS.md"):
        if not (ROOT / rel).exists():
            errors.append(f"Missing required regression-guard file: {rel}")
    return errors


def main() -> int:
    errors = []
    errors.extend(check_guard_files())
    errors.extend(check_python_compile())
    errors.extend(check_config())
    errors.extend(check_frontend_js())
    if errors:
        print("REGRESSION GUARD CHECK FAILED:")
        for e in errors:
            print(f" - {e}")
        return 1
    print("REGRESSION GUARD CHECK PASSED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
