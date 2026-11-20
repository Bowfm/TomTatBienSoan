"""Module tự động cập nhật mã nguồn và tài nguyên công cụ từ GitHub."""
from __future__ import annotations

import io
import json
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

# Các thư mục và file của người dùng tuyệt đối KHÔNG ĐƯỢC GHI ĐÈ khi cập nhật
PROTECTED_ENTRIES = {
    "input",
    "output",
    "work",
    ".venv",
    ".env",
    "api_keys.txt",
}

# File cấu hình cục bộ riêng của từng máy (không phải thư mục cốt lõi trong PROTECTED_ENTRIES)
LOCAL_ONLY_FILES = {
    "config.local.yaml",
}

_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")


def _is_valid_sha(value: str) -> bool:
    return bool(value and _SHA_RE.fullmatch(value.strip()))


def _get_repo_url(root: Path) -> str:
    url_file = root / ".github_repo_url"
    repo_url = "https://github.com/Bowfm/TomTatBienSoan.git"
    if url_file.exists():
        try:
            c = url_file.read_text(encoding="utf-8-sig").strip()
            if c:
                repo_url = c
        except Exception:
            pass
    return repo_url


def _parse_git_info_refs_sha(raw: bytes, branch: str = "main") -> str:
    """Trích xuất commit SHA từ phản hồi Git Smart HTTP /info/refs?service=git-upload-pack."""
    lines: list[str] = []
    pos = 0
    n = len(raw)
    while pos + 4 <= n:
        hdr = raw[pos:pos + 4]
        try:
            pkt_len = int(hdr, 16)
        except ValueError:
            break
        if pkt_len == 0:
            pos += 4
            continue
        if pkt_len < 4 or pos + pkt_len > n:
            break
        payload = raw[pos + 4:pos + pkt_len].decode("utf-8", errors="replace").rstrip("\r\n")
        lines.append(payload)
        pos += pkt_len

    target_ref = f"refs/heads/{branch}"
    head_sha = ""

    def _scan(candidates: list[str]) -> str:
        nonlocal head_sha
        for line in candidates:
            clean_line = line.split("\x00", 1)[0].strip()
            parts = clean_line.split()
            if len(parts) >= 2 and _is_valid_sha(parts[0]):
                sha, ref = parts[0].lower(), parts[1]
                if ref == target_ref:
                    return sha
                if ref == "HEAD" and not head_sha:
                    head_sha = sha
        return ""

    found = _scan(lines)
    if found:
        return found

    # Fallback khi proxy/server làm lệch độ dài pkt-line hoặc trả text thuần
    text = raw.decode("utf-8", errors="replace")
    m_main = re.search(rf"([0-9a-fA-F]{{40}})\s+{re.escape(target_ref)}(?:\r|\n|$|\x00)", text)
    if m_main:
        return m_main.group(1).lower()
    if not head_sha:
        m_head = re.search(r"([0-9a-fA-F]{40})\s+HEAD(?:\x00|\s|$)", text)
        if m_head:
            head_sha = m_head.group(1).lower()
    return head_sha


def _ssl_contexts() -> list[ssl.SSLContext | None]:
    contexts: list[ssl.SSLContext | None] = []
    try:
        import certifi
        contexts.append(ssl.create_default_context(cafile=certifi.where()))
    except Exception:
        contexts.append(None)
    contexts.append(ssl._create_unverified_context())
    return contexts


def _is_dns_offline_error(exc: Exception) -> bool:
    reason = getattr(exc, "reason", exc)
    return isinstance(reason, socket.gaierror)


def _read_version_commit(root: Path) -> str:
    v_file = root / "version.json"
    if v_file.exists():
        try:
            data = json.loads(v_file.read_text(encoding="utf-8-sig"))
            if data.get("commit"):
                return str(data["commit"]).strip()
        except Exception:
            pass
    return ""


def _is_git_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    """Kiểm tra xem commit `ancestor` đã nằm trong lịch sử của `descendant` chưa."""
    if not ancestor or not descendant or not (root / ".git").exists():
        return False
    try:
        res = subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            capture_output=True,
            text=True,
            cwd=str(root),
            timeout=5,
        )
        return res.returncode == 0
    except Exception:
        return False


def get_current_commit(root_dir: Path | str | None = None) -> str:
    root = Path(root_dir) if root_dir else Path.cwd()
    if (root / ".git").exists():
        try:
            res = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=str(root), timeout=5)
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception:
            pass
    return _read_version_commit(root)


def get_remote_commit(root_dir: Path | str | None = None) -> str:
    root = Path(root_dir) if root_dir else Path.cwd()
    repo_url = _get_repo_url(root)
    clean = repo_url.rstrip("/").removesuffix(".git")
    parts = clean.split("/")
    owner_repo = f"{parts[-2]}/{parts[-1]}" if len(parts) >= 2 else "Bowfm/TomTatBienSoan"

    info_refs_url = f"{clean}.git/info/refs?service=git-upload-pack"
    api_url = f"https://api.github.com/repos/{owner_repo}/commits/main"
    contexts = _ssl_contexts()
    has_proxies = bool(urllib.request.getproxies())

    # Lớp 1: Git Smart HTTP (không giới hạn 60 req/giờ của REST API, cập nhật tức thì sau git push, không cần .git/git.exe)
    req_git = urllib.request.Request(
        info_refs_url,
        headers={
            "User-Agent": "git/2.40.0",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
    )
    for ctx in contexts:
        try:
            with urllib.request.urlopen(req_git, timeout=4, context=ctx) as resp:
                sha = _parse_git_info_refs_sha(resp.read(), branch="main")
                if sha:
                    return sha
            break
        except Exception as exc:
            if _is_dns_offline_error(exc) and not has_proxies:
                return ""

    # Lớp 2: GitHub REST API (kèm chống cache & kiểm tra định dạng SHA)
    req_api = urllib.request.Request(
        api_url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TomTatUpdater/2.0",
            "Accept": "application/vnd.github.sha",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
    )
    for ctx in contexts:
        try:
            with urllib.request.urlopen(req_api, timeout=4, context=ctx) as resp:
                sha = resp.read().decode("utf-8", errors="replace").strip()
                if _is_valid_sha(sha):
                    return sha.lower()
            break
        except Exception as exc:
            if _is_dns_offline_error(exc) and not has_proxies:
                return ""

    # Lớp 3: git ls-remote (hoạt động cả khi không có .git nếu chỉ định URL trực tiếp)
    if shutil.which("git"):
        remote_target = "origin" if (root / ".git").exists() else repo_url
        try:
            res = subprocess.run(
                ["git", "ls-remote", remote_target, "refs/heads/main"],
                capture_output=True,
                text=True,
                cwd=str(root),
                timeout=6,
            )
            if res.returncode == 0 and res.stdout.strip():
                sha = res.stdout.strip().split()[0]
                if _is_valid_sha(sha):
                    return sha.lower()
        except Exception:
            pass

    # Lớp 4: curl.exe tích hợp Windows (nếu urllib bị lỗi SSL/proxy)
    curl_exe = shutil.which("curl.exe") or shutil.which("curl")
    if curl_exe:
        try:
            curl_res = subprocess.run(
                [
                    curl_exe,
                    "-sSL",
                    "-k",
                    "-H",
                    "Cache-Control: no-cache",
                    "--connect-timeout",
                    "4",
                    "--max-time",
                    "6",
                    info_refs_url,
                ],
                capture_output=True,
                timeout=8,
            )
            if curl_res.returncode == 0 and curl_res.stdout:
                sha = _parse_git_info_refs_sha(curl_res.stdout, branch="main")
                if sha:
                    return sha
        except Exception:
            pass

    return ""


def _is_python_runtime_file(rel_path: str) -> bool:
    norm = rel_path.replace("\\", "/")
    name = norm.rsplit("/", 1)[-1]
    return norm.endswith(".py") or name in ("requirements.txt", "pyproject.toml")


def _fetch_update_summary(root: Path, current: str, remote: str) -> dict:
    """Lấy tóm tắt commit mới trên GitHub (best-effort, không bao giờ ném ngoại lệ)."""
    if len(remote.strip()) != 40:
        return {}
    repo_url = _get_repo_url(root)
    clean = repo_url.rstrip("/").removesuffix(".git")
    parts = clean.split("/")
    owner_repo = f"{parts[-2]}/{parts[-1]}" if len(parts) >= 2 else "Bowfm/TomTatBienSoan"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TomTatUpdater/2.0",
        "Accept": "application/vnd.github+json",
        "Cache-Control": "no-cache",
    }
    contexts = _ssl_contexts()
    has_proxies = bool(urllib.request.getproxies())

    def _get_json(url: str) -> dict | None:
        req = urllib.request.Request(url, headers=headers)
        for ctx in contexts:
            try:
                with urllib.request.urlopen(req, timeout=2.5, context=ctx) as resp:
                    data = json.loads(resp.read().decode("utf-8", errors="replace"))
                    return data if isinstance(data, dict) else None
            except Exception as exc:
                if _is_dns_offline_error(exc) and not has_proxies:
                    return None
        return None

    if len(current.strip()) == 40:
        cmp_data = _get_json(f"https://api.github.com/repos/{owner_repo}/compare/{current.strip()}...{remote.strip()}")
        if cmp_data and isinstance(cmp_data.get("commits"), list) and cmp_data["commits"]:
            commits = cmp_data["commits"]
            titles = [
                str((c.get("commit") or {}).get("message") or "").splitlines()[0].strip()
                for c in commits
                if str((c.get("commit") or {}).get("message") or "").strip()
            ]
            latest_msg = titles[-1][:140] if titles else ""
            return {
                "ahead_by": int(cmp_data.get("ahead_by") or len(commits)),
                "latest_message": latest_msg,
                "commits_preview": titles[-5:][::-1],
            }

    commit_data = _get_json(f"https://api.github.com/repos/{owner_repo}/commits/{remote.strip()}")
    if commit_data:
        raw_msg = str((commit_data.get("commit") or {}).get("message") or "").strip()
        if raw_msg:
            first_line = raw_msg.splitlines()[0].strip()[:140]
            return {
                "ahead_by": 1,
                "latest_message": first_line,
                "commits_preview": [first_line],
            }
    return {}


def _startup_cache_path(root: Path) -> Path:
    return root / "work" / ".startup_update_cache.json"


def _write_startup_update_cache(root: Path, commit: str, info: dict) -> None:
    try:
        p = _startup_cache_path(root)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {"ts": time.time(), "commit": commit, "info": info}
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def consume_startup_update_cache(
    root_dir: Path | str | None = None,
    *,
    max_age_s: float = 15.0,
) -> dict | None:
    """Đọc và xóa cache kết quả check_update vừa chạy lúc khởi động (tránh gọi mạng 2 lần)."""
    root = Path(root_dir) if root_dir else Path.cwd()
    p = _startup_cache_path(root)
    if not p.exists():
        return None
    try:
        raw = p.read_text(encoding="utf-8-sig")
    except Exception:
        raw = ""
    try:
        p.unlink(missing_ok=True)
    except Exception:
        pass
    if not raw:
        return None
    try:
        data = json.loads(raw)
        ts = float(data.get("ts") or 0.0)
        if (time.time() - ts) > max_age_s or (time.time() - ts) < -5.0:
            return None
        cached_commit = str(data.get("commit") or "").strip()
        if cached_commit != get_current_commit(root):
            return None
        info = data.get("info")
        if isinstance(info, dict) and "has_update" in info:
            return dict(info)
    except Exception:
        pass
    return None


def _save_version(root: Path, commit: str) -> None:
    if not commit:
        return
    try:
        from datetime import datetime
        v_file = root / "version.json"
        data = {
            "commit": commit.strip(),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        v_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def check_update(
    root_dir: Path | str | None = None,
    *,
    cache_for_startup: bool = False,
    include_details: bool = False,
) -> dict:
    """Kiểm tra xem bản hiện tại có khác biệt với bản trên GitHub không."""
    root = Path(root_dir) if root_dir else Path.cwd()
    current = get_current_commit(root)
    remote = get_remote_commit(root)

    cur_short = current[:7] if current else ""
    rem_short = remote[:7] if remote else ""

    if not remote:
        res = {
            "has_update": False,
            "status": "offline",
            "current_commit": cur_short,
            "latest_commit": "",
            "message": "Không thể kết nối đến GitHub để kiểm tra phiên bản.",
        }
        if cache_for_startup:
            _write_startup_update_cache(root, current, res)
        return res

    if not current:
        res = {
            "has_update": True,
            "status": "update_available",
            "current_commit": "",
            "latest_commit": rem_short,
            "message": f"Có bản cập nhật mới trên GitHub (bản mới: {rem_short})",
        }
        if include_details:
            res.update(_fetch_update_summary(root, current, remote))
        if cache_for_startup:
            _write_startup_update_cache(root, current, res)
        return res

    v_commit = _read_version_commit(root)
    v_short = v_commit[:7] if v_commit else ""

    if (
        cur_short.lower() == rem_short.lower()
        or current.lower() == remote.lower()
        or (v_short and v_short.lower() == rem_short.lower())
    ):
        res = {
            "has_update": False,
            "status": "up_to_date",
            "current_commit": cur_short,
            "latest_commit": rem_short,
            "message": f"Đã là phiên bản mới nhất ({cur_short}).",
        }
        if cache_for_startup:
            _write_startup_update_cache(root, current, res)
        return res

    # Nếu bản trên GitHub là tổ tiên của HEAD hiện tại (máy local có commit mới hơn chưa push lên GitHub)
    if _is_git_ancestor(root, remote, current):
        res = {
            "has_update": False,
            "status": "up_to_date",
            "current_commit": cur_short,
            "latest_commit": rem_short,
            "message": f"Đã là phiên bản mới nhất ({cur_short}, mới hơn GitHub {rem_short}).",
        }
        if cache_for_startup:
            _write_startup_update_cache(root, current, res)
        return res

    res = {
        "has_update": True,
        "status": "update_available",
        "current_commit": cur_short,
        "latest_commit": rem_short,
        "message": f"Có bản cập nhật mới trên GitHub! (bản mới: {rem_short}, hiện tại: {cur_short})",
    }
    if include_details:
        res.update(_fetch_update_summary(root, current, remote))
    if cache_for_startup:
        _write_startup_update_cache(root, current, res)
    return res


def _read_manifest(root: Path) -> list[str]:
    mf = root / "work" / ".updater_manifest.json"
    if not mf.exists():
        return []
    try:
        data = json.loads(mf.read_text(encoding="utf-8-sig"))
        files = data.get("files") if isinstance(data, dict) else None
        if isinstance(files, list):
            return [str(x).replace("\\", "/") for x in files if x]
    except Exception:
        pass
    return []


def _write_manifest(root: Path, files: set[str], commit: str) -> None:
    try:
        mf = root / "work" / ".updater_manifest.json"
        mf.parent.mkdir(parents=True, exist_ok=True)
        payload = {"commit": commit, "files": sorted(files)}
        mf.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def run_update_detailed(root_dir: Path | str | None = None) -> dict:
    """Cập nhật công cụ lên bản mới nhất từ kho GitHub và trả về chi tiết các thay đổi.
    1. Nếu có thư mục .git và có git trong PATH: chạy `git pull`.
    2. Nếu không có git (hoặc máy clone từ zip): tải file archive ZIP từ GitHub,
       giải nén cập nhật mã nguồn/prompt/scripts, dọn file cũ đã xóa (bảo vệ 100% dữ liệu).
    """
    root = Path(root_dir) if root_dir else Path.cwd()
    root_resolved = root.resolve()

    # Cách 1: Sử dụng Git nếu có thư mục .git
    git_dir = root / ".git"
    if git_dir.exists():
        try:
            current_sha = get_current_commit(root)
            remote_sha = get_remote_commit(root)
            v_commit = _read_version_commit(root)
            if remote_sha and (
                (current_sha and (
                    current_sha[:7].lower() == remote_sha[:7].lower()
                    or _is_git_ancestor(root, remote_sha, current_sha)
                ))
                or (v_commit and v_commit[:7].lower() == remote_sha[:7].lower())
            ):
                cur_disp = current_sha[:7] if current_sha else v_commit[:7]
                return {
                    "ok": True,
                    "message": f"[Git] Đã ở phiên bản mới nhất ({cur_disp}).",
                    "method": "git",
                    "updated": False,
                    "changed_python": False,
                    "current_commit": current_sha or v_commit,
                    "removed_files": [],
                }

            if shutil.which("git"):
                v_file = root / "version.json"
                prev_v_bytes = v_file.read_bytes() if v_file.exists() else None
                # Khôi phục version.json nếu bị sửa tạm để không chặn git pull
                subprocess.run(["git", "checkout", "--", "version.json"], capture_output=True, cwd=str(root))
                pull = subprocess.run(
                    ["git", "pull", "--ff-only", "origin", "main"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    cwd=str(root),
                )
                out = (pull.stdout or "") + ("\n" + pull.stderr if pull.stderr else "")
                if pull.returncode == 0:
                    after_sha = get_current_commit(root)
                    updated = bool(after_sha and after_sha != current_sha)
                    changed_python = False
                    need_pip = False
                    if updated and current_sha:
                        diff_res = subprocess.run(
                            ["git", "diff", "--name-only", current_sha, after_sha],
                            capture_output=True,
                            text=True,
                            cwd=str(root),
                            timeout=10,
                        )
                        if diff_res.returncode == 0:
                            changed_files = [ln.strip() for ln in diff_res.stdout.splitlines() if ln.strip()]
                            changed_python = any(_is_python_runtime_file(f) for f in changed_files)
                            need_pip = any(Path(f).name == "requirements.txt" for f in changed_files)
                        else:
                            changed_python = True
                            need_pip = True
                    elif updated:
                        changed_python = True
                        need_pip = True

                    if need_pip:
                        _update_pip(root)
                    return {
                        "ok": True,
                        "message": f"[Git] Đã cập nhật thành công từ GitHub:\n{out.strip()}",
                        "method": "git",
                        "updated": updated,
                        "changed_python": changed_python,
                        "current_commit": after_sha or current_sha,
                        "removed_files": [],
                    }
                if prev_v_bytes is not None:
                    v_file.write_bytes(prev_v_bytes)
        except Exception:
            pass

    # Cách 2: Tải trực tiếp ZIP từ GitHub (cho máy dùng bản chia sẻ không có Git)
    repo_url = _get_repo_url(root)

    clean_url = repo_url.rstrip("/").removesuffix(".git")
    zip_url = f"{clean_url}/archive/refs/heads/main.zip"

    zip_bytes = None
    last_err = None

    # Tải archive ZIP từ GitHub với nhiều lớp bọc dự phòng (chống lỗi SSL trên máy Windows)
    ssl_context = None
    try:
        import certifi
        ssl_context = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass

    req = urllib.request.Request(
        zip_url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TomTatUpdater/2.0",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
    )

    # Lớp 1: urllib với SSL mặc định / certifi
    try:
        with urllib.request.urlopen(req, timeout=30, context=ssl_context) as resp:
            zip_bytes = resp.read()
    except Exception as exc:
        last_err = exc

    # Lớp 2: urllib với unverified SSL (nếu máy tính thiếu chứng chỉ root CA)
    if zip_bytes is None:
        try:
            unverified_ctx = ssl._create_unverified_context()
            with urllib.request.urlopen(req, timeout=30, context=unverified_ctx) as resp:
                zip_bytes = resp.read()
        except Exception as exc:
            last_err = exc

    # Lớp 3: curl.exe (tích hợp sẵn trên Windows 10/11)
    if zip_bytes is None:
        try:
            curl_res = subprocess.run(
                ["curl.exe", "-sSL", "-k", zip_url],
                capture_output=True,
                timeout=45,
            )
            if curl_res.returncode == 0 and len(curl_res.stdout) > 1000:
                zip_bytes = curl_res.stdout
        except Exception as exc:
            last_err = exc

    # Lớp 4: PowerShell WebClient
    if zip_bytes is None:
        try:
            ps_script = (
                "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; "
                "[System.Net.ServicePointManager]::ServerCertificateValidationCallback = {$true}; "
                f"$wc = New-Object System.Net.WebClient; $wc.DownloadData('{zip_url}')"
            )
            ps_res = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True,
                timeout=45,
            )
            if ps_res.returncode == 0 and len(ps_res.stdout) > 1000:
                zip_bytes = ps_res.stdout
        except Exception as exc:
            last_err = exc

    if not zip_bytes:
        return {
            "ok": False,
            "message": f"Không thể cập nhật từ {zip_url}: {last_err}",
            "method": "zip",
            "updated": False,
            "changed_python": False,
            "current_commit": get_current_commit(root),
            "removed_files": [],
        }

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        namelist = zf.namelist()
        if not namelist:
            return {
                "ok": False,
                "message": "File ZIP tải về từ GitHub bị rỗng.",
                "method": "zip",
                "updated": False,
                "changed_python": False,
                "current_commit": get_current_commit(root),
                "removed_files": [],
            }

        prefix = namelist[0].split("/")[0] + "/"
        prev_manifest = _read_manifest(root)
        new_manifest: set[str] = set()
        protected_top = PROTECTED_ENTRIES | LOCAL_ONLY_FILES

        updated_files = 0
        changed_python = False
        need_pip = False
        for member in zf.infolist():
            rel_name = member.filename
            if rel_name.startswith(prefix):
                rel_name = rel_name[len(prefix):]
            if not rel_name:
                continue

            norm_rel = rel_name.replace("\\", "/").lstrip("/")
            first_part = norm_rel.split("/")[0]
            if first_part in protected_top:
                continue

            dest_path = (root / norm_rel).resolve()
            if not dest_path.is_relative_to(root_resolved):
                continue

            if member.is_dir():
                dest_path.mkdir(parents=True, exist_ok=True)
            else:
                new_manifest.add(norm_rel)
                with zf.open(member) as src_f:
                    new_bytes = src_f.read()
                old_bytes = dest_path.read_bytes() if dest_path.exists() else None
                if old_bytes != new_bytes:
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    dest_path.write_bytes(new_bytes)
                    updated_files += 1
                    if _is_python_runtime_file(norm_rel):
                        changed_python = True
                    if Path(norm_rel).name == "requirements.txt":
                        need_pip = True

        # Dọn các file mã nguồn/tài nguyên đã bị xóa hoặc đổi tên trên GitHub (chỉ xét file thuộc manifest trước)
        removed_files: list[str] = []
        for old_rel in prev_manifest:
            if old_rel in new_manifest or old_rel == "version.json":
                continue
            first_part = old_rel.split("/")[0]
            if first_part in protected_top:
                continue
            old_path = (root / old_rel).resolve()
            if not old_path.is_relative_to(root_resolved) or not old_path.is_file():
                continue
            try:
                old_path.unlink()
                removed_files.append(old_rel)
                if _is_python_runtime_file(old_rel):
                    changed_python = True
                # Dọn thư mục cha rỗng
                parent = old_path.parent
                while parent != root_resolved:
                    try:
                        parent.rmdir()
                        parent = parent.parent
                    except OSError:
                        break
            except Exception:
                pass

        zip_commit = ""
        if zf.comment:
            try:
                candidate = zf.comment.decode("utf-8", errors="replace").strip()
                if _is_valid_sha(candidate):
                    zip_commit = candidate
            except Exception:
                pass
        if not zip_commit:
            zip_commit = get_remote_commit(root)

        _save_version(root, zip_commit)
        _write_manifest(root, new_manifest, zip_commit)
        if need_pip:
            _update_pip(root)
        cur_short = zip_commit[:7] if zip_commit else "mới nhất"
        extra_rm = f", dọn {len(removed_files)} file cũ" if removed_files else ""
        return {
            "ok": True,
            "message": f"[GitHub Archive] Đã cập nhật thành công {updated_files} files{extra_rm} (phiên bản {cur_short}) từ kho GitHub!",
            "method": "zip",
            "updated": (updated_files > 0 or len(removed_files) > 0),
            "changed_python": changed_python,
            "current_commit": zip_commit,
            "removed_files": removed_files,
        }
    except Exception as exc:
        return {
            "ok": False,
            "message": f"Không thể cập nhật từ {zip_url}: {exc}",
            "method": "zip",
            "updated": False,
            "changed_python": False,
            "current_commit": get_current_commit(root),
            "removed_files": [],
        }


def run_update(root_dir: Path | str | None = None) -> tuple[bool, str]:
    """Cập nhật công cụ lên bản mới nhất từ kho GitHub (trả về `(ok, message)`)."""
    res = run_update_detailed(root_dir)
    return bool(res["ok"]), str(res["message"])


def _update_pip(root: Path) -> None:
    req_file = root / "requirements.txt"
    py_exe = root / ".venv" / "Scripts" / "python.exe"
    if py_exe.exists() and req_file.exists():
        try:
            subprocess.run(
                [str(py_exe), "-m", "pip", "install", "-r", str(req_file), "-q"],
                capture_output=True,
                cwd=str(root),
                timeout=60,
            )
        except Exception:
            pass


if __name__ == "__main__":
    ok, msg = run_update()
    print(msg)
    sys.exit(0 if ok else 1)
