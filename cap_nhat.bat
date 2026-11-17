@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

REM --- Cách 1: Nếu thư mục có sẵn Git (.git), chạy git pull cực nhanh ---
if exist ".git" (
    where git >nul 2>nul
    if not errorlevel 1 (
        echo [TU DONG CAP NHAT] Dang kiem tra ban cap nhat moi tu GitHub...
        git fetch origin main >nul 2>nul
        git checkout -- prompts src tests *.bat *.md *.txt requirements.txt >nul 2>nul
        git pull --ff-only origin main
        if exist ".venv\Scripts\python.exe" (
            ".venv\Scripts\python.exe" -m pip install -r requirements.txt -q >nul 2>nul
        )
        exit /b 0
    )
)

REM --- Cách 2: Không cần cài Git - tải trực tiếp bản mới từ link trong .github_repo_url ---
if exist ".github_repo_url" (
    set /p REPO_URL=<".github_repo_url"
    echo [TU DONG CAP NHAT] Dang tai ban cap nhat moi nhat...
    powershell -NoProfile -Command ^
      "$u = ('%REPO_URL%').Trim() -replace '\.git$',''; " ^
      "if (-not $u) { exit 0 }; " ^
      "$zipUrl = $u + '/archive/refs/heads/main.zip'; " ^
      "$tmpZip = Join-Path $env:TEMP 'tomtat_update.zip'; " ^
      "$tmpDir = Join-Path $env:TEMP 'tomtat_update_extract'; " ^
      "try { " ^
      "  Invoke-WebRequest -UseBasicParsing -Uri $zipUrl -OutFile $tmpZip -TimeoutSec 15; " ^
      "  if (Test-Path $tmpDir) { Remove-Item $tmpDir -Recurse -Force }; " ^
      "  Expand-Archive -Path $tmpZip -DestinationPath $tmpDir -Force; " ^
      "  $sub = Get-ChildItem $tmpDir -Directory | Select-Object -First 1; " ^
      "  if ($sub) { " ^
      "    foreach ($it in @('prompts','src','tests','requirements.txt','setup.bat','tomtat.bat','khoi_dong.bat','tat_server.bat','cap_nhat.bat','README.md','HUONG_DAN_CAI_DAT_NHANH.txt')) { " ^
      "      $s = Join-Path $sub.FullName $it; " ^
      "      if (Test-Path $s) { Copy-Item -Path $s -Destination (Join-Path '%~dp0' $it) -Recurse -Force } " ^
      "    }; " ^
      "    Write-Host '[OK] Da cap nhat thanh cong ban moi nhat!' " ^
      "  } " ^
      "} catch { Write-Host '[Bo qua cap nhat] Khong ket noi duoc kho cap nhat.' } " ^
      "finally { " ^
      "  Remove-Item $tmpZip -Force -ErrorAction SilentlyContinue; " ^
      "  Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue; " ^
      "}"
)
endlocal
