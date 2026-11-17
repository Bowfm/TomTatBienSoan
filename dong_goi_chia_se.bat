@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "OUT_ZIP=%~dp0TomTatBienSoan_ChiaSe.zip"
if exist "%OUT_ZIP%" del /f /q "%OUT_ZIP%"

echo ============================================================
echo   DONG GOI CONG CU DE CHIA SE / MANG SANG MAY KHAC
echo ============================================================
echo Dang tao file %OUT_ZIP% (kem tinh nang tu dong cap nhat)...

powershell -NoProfile -Command ^
  "$root = '%~dp0'.TrimEnd('\'); " ^
  "$outZip = '%OUT_ZIP%'; " ^
  "$tmp = Join-Path $env:TEMP ('TomTatBienSoan_pack_' + [guid]::NewGuid().ToString('N')); " ^
  "$stage = Join-Path $tmp 'TomTatBienSoan'; " ^
  "New-Item -ItemType Directory -Force -Path $stage | Out-Null; " ^
  "$items = @('HUONG_DAN_CAI_DAT_NHANH.txt','README.md','config.yaml','.env.example','.gitignore','.github_repo_url','requirements.txt','setup.bat','tomtat.bat','khoi_dong.bat','tat_server.bat','cap_nhat.bat','day_cap_nhat.bat','dong_goi_chia_se.bat','prompts','src','tests','docs','skill_ref','samples'); " ^
  "foreach ($it in $items) { " ^
  "  $src = Join-Path $root $it; " ^
  "  if (Test-Path $src) { Copy-Item -Path $src -Destination (Join-Path $stage $it) -Recurse -Force } " ^
  "}; " ^
  "foreach ($d in @('input','output','work')) { New-Item -ItemType Directory -Force -Path (Join-Path $stage $d) | Out-Null }; " ^
  "$mem = Join-Path $root 'work\learned_memory.json'; " ^
  "if (Test-Path $mem) { Copy-Item -Path $mem -Destination (Join-Path $stage 'work\learned_memory.json') -Force }; " ^
  "Get-ChildItem -Path $stage -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue; " ^
  "Compress-Archive -Path $stage -DestinationPath $outZip -Force; " ^
  "Remove-Item -Path $tmp -Recurse -Force -ErrorAction SilentlyContinue;"

if exist "%OUT_ZIP%" (
    echo.
    echo [THANH CONG] Da tao goi chia se tai:
    echo   %OUT_ZIP%
) else (
    echo [LOI] Khong tao duoc file ZIP.
)
pause
endlocal
