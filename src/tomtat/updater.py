"""Module tự động cập nhật mã nguồn và tài nguyên công cụ từ GitHub."""
from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
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


def _read_version_commit(root: Path) -> str:
    v_file = root / "version.json"
    if v_file.exists():
        try:
            data = json.loads(v_file.read_text(encoding="utf-8"))
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
    url_file = root / ".github_repo_url"
    repo_url = "https://github.com/Bowfm/TomTatBienSoan.git"
    if url_file.exists():
        try:
            c = url_file.read_text(encoding="utf-8").strip()
            if c:
                repo_url = c
        except Exception:
            pass

    clean = repo_url.rstrip("/").removesuffix(".git")
    parts = clean.split("/")
    owner_repo = f"{parts[-2]}/{parts[-1]}" if len(parts) >= 2 else "Bowfm/TomTatBienSoan"
    api_url = f"https://api.github.com/repos/{owner_repo}/commits/main"

    import ssl
    req = urllib.request.Request(
        api_url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TomTatUpdater/2.0",
            "Accept": "application/vnd.github.sha",
        },
    )
    try:
        ctx = ssl._create_unverified_context()
        with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
            sha = resp.read().decode("utf-8").strip()
            if sha and len(sha) >= 7:
                return sha
    except Exception:
        pass

    if (root / ".git").exists():
        try:
            res = subprocess.run(["git", "ls-remote", "origin", "main"], capture_output=True, text=True, cwd=str(root), timeout=7)
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip().split()[0]
        except Exception:
            pass

    return ""


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


def check_update(root_dir: Path | str | None = None) -> dict:
    """Kiểm tra xem bản hiện tại có khác biệt với bản trên GitHub không."""
    root = Path(root_dir) if root_dir else Path.cwd()
    current = get_current_commit(root)
    remote = get_remote_commit(root)

    cur_short = current[:7] if current else ""
    rem_short = remote[:7] if remote else ""

    if not remote:
        return {
            "has_update": False,
            "status": "offline",
            "current_commit": cur_short,
            "latest_commit": "",
            "message": "Không thể kết nối đến GitHub để kiểm tra phiên bản.",
        }

    if not current:
        return {
            "has_update": True,
            "status": "update_available",
            "current_commit": "",
            "latest_commit": rem_short,
            "message": f"Có bản cập nhật mới trên GitHub (bản mới: {rem_short})",
        }

    v_commit = _read_version_commit(root)
    v_short = v_commit[:7] if v_commit else ""

    if (
        cur_short.lower() == rem_short.lower()
        or current.lower() == remote.lower()
        or (v_short and v_short.lower() == rem_short.lower())
    ):
        return {
            "has_update": False,
            "status": "up_to_date",
            "current_commit": cur_short,
            "latest_commit": rem_short,
            "message": f"Đã là phiên bản mới nhất ({cur_short}).",
        }

    # Nếu bản trên GitHub là tổ tiên của HEAD hiện tại (máy local có commit mới hơn chưa push lên GitHub)
    if _is_git_ancestor(root, remote, current):
        return {
            "has_update": False,
            "status": "up_to_date",
            "current_commit": cur_short,
            "latest_commit": rem_short,
            "message": f"Đã là phiên bản mới nhất ({cur_short}, mới hơn GitHub {rem_short}).",
        }

    return {
        "has_update": True,
        "status": "update_available",
        "current_commit": cur_short,
        "latest_commit": rem_short,
        "message": f"Có bản cập nhật mới trên GitHub! (bản mới: {rem_short}, hiện tại: {cur_short})",
    }


def run_update(root_dir: Path | str | None = None) -> tuple[bool, str]:
    """Cập nhật công cụ lên bản mới nhất từ kho GitHub:
    1. Nếu có thư mục .git và có git trong PATH: chạy `git pull`.
    2. Nếu không có git (hoặc máy clone từ zip): tải file archive ZIP từ GitHub,
       giải nén và ghi đè các file mã nguồn, prompt, scripts (bảo vệ 100% dữ liệu).
    """
    root = Path(root_dir) if root_dir else Path.cwd()

    # Cách 1: Sử dụng Git nếu có thư mục .git
    git_dir = root / ".git"
    if git_dir.exists():
        try:
            current_sha = get_current_commit(root)
            remote_sha = get_remote_commit(root)
            if remote_sha and current_sha and (
                current_sha[:7].lower() == remote_sha[:7].lower()
                or _is_git_ancestor(root, remote_sha, current_sha)
            ):
                _save_version(root, current_sha)
                return True, f"[Git] Đã ở phiên bản mới nhất ({current_sha[:7]})."

            where_res = subprocess.run(["where", "git"], capture_output=True, text=True, cwd=str(root))
            if where_res.returncode == 0:
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
                    _update_pip(root)
                    new_sha = get_current_commit(root)
                    _save_version(root, new_sha)
                    return True, f"[Git] Đã cập nhật thành công từ GitHub:\n{out.strip()}"
        except Exception:
            pass

    # Cách 2: Tải trực tiếp ZIP từ GitHub (cho máy dùng bản chia sẻ không có Git)
    url_file = root / ".github_repo_url"
    repo_url = "https://github.com/Bowfm/TomTatBienSoan.git"
    if url_file.exists():
        try:
            c = url_file.read_text(encoding="utf-8").strip()
            if c:
                repo_url = c
        except Exception:
            pass

    clean_url = repo_url.rstrip("/").removesuffix(".git")
    zip_url = f"{clean_url}/archive/refs/heads/main.zip"

    zip_bytes = None
    last_err = None

    # Tải archive ZIP từ GitHub với nhiều lớp bọc dự phòng (chống lỗi SSL trên máy Windows)
    import ssl

    ssl_context = None
    try:
        import certifi
        ssl_context = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass

    req = urllib.request.Request(
        zip_url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TomTatUpdater/2.0"},
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
        return False, f"Không thể cập nhật từ {zip_url}: {last_err}"

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        namelist = zf.namelist()
        if not namelist:
            return False, "File ZIP tải về từ GitHub bị rỗng."

        prefix = namelist[0].split("/")[0] + "/"

        updated_files = 0
        for member in zf.infolist():
            rel_name = member.filename
            if rel_name.startswith(prefix):
                rel_name = rel_name[len(prefix):]
            if not rel_name:
                continue

            first_part = rel_name.split("/")[0].split("\\")[0]
            if first_part in PROTECTED_ENTRIES:
                continue

            dest_path = root / rel_name
            if member.is_dir():
                dest_path.mkdir(parents=True, exist_ok=True)
            else:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src_f, open(dest_path, "wb") as dst_f:
                    shutil.copyfileobj(src_f, dst_f)
                updated_files += 1

        zip_commit = ""
        if zf.comment:
            try:
                zip_commit = zf.comment.decode("utf-8").strip()
            except Exception:
                pass
        if not zip_commit:
            zip_commit = get_remote_commit(root)

        _save_version(root, zip_commit)
        _update_pip(root)
        cur_short = zip_commit[:7] if zip_commit else "mới nhất"
        return True, f"[GitHub Archive] Đã cập nhật thành công {updated_files} files (phiên bản {cur_short}) từ kho GitHub!"
    except Exception as exc:
        return False, f"Không thể cập nhật từ {zip_url}: {exc}"


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
