@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================================================
echo   TU DONG CAP NHAT CONG CU "TOM TAT - BIEN SOAN" TU GITHUB
echo ============================================================================
echo.

if exist ".venv\Scripts\python.exe" (
    echo Dang kiem tra va tai ban cap nhat moi tu GitHub...
    ".venv\Scripts\python.exe" -c "import sys; sys.path.insert(0, 'src'); from tomtat.updater import run_update; ok, msg = run_update(); print(msg); sys.exit(0 if ok else 1)"
    if errorlevel 1 (
        echo.
        echo [LOI] Cap nhat that bai. Vui long kiem tra lai ket noi Internet.
        pause
        exit /b 1
    )
    echo.
    echo ============================================================================
    echo   [THANH CONG] DA CAP NHAT BAN MOI NHAT TU GITHUB!
    echo ============================================================================
    pause
    exit /b 0
)

if exist ".git" (
    where git >nul 2>nul
    if not errorlevel 1 (
        echo Dang cap nhat qua Git...
        git fetch origin main >nul 2>nul
        git pull --ff-only origin main
        echo.
        echo [THANH CONG] Da cap nhat thanh cong qua Git!
        pause
        exit /b 0
    )
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "$root = '%~dp0'.TrimEnd('\'); $urlFile = Join-Path $root '.github_repo_url'; if (Test-Path $urlFile) { $u = (Get-Content $urlFile -Raw).Trim() -replace '\.git$',''; $zipUrl = $u + '/archive/refs/heads/main.zip'; $tmpZip = Join-Path $env:TEMP ('tomtat_' + [guid]::NewGuid().ToString('N') + '.zip'); $tmpDir = Join-Path $env:TEMP ('tomtat_' + [guid]::NewGuid().ToString('N')); try { Write-Host 'Dang tai ban cap nhat tu GitHub...'; [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri $zipUrl -OutFile $tmpZip -TimeoutSec 30; Expand-Archive -Path $tmpZip -DestinationPath $tmpDir -Force; $sub = Get-ChildItem $tmpDir -Directory | Select-Object -First 1; if ($sub) { foreach ($item in Get-ChildItem $sub.FullName) { if ($item.Name -notin @('input','output','work','.venv','api_keys.txt','.env')) { Copy-Item -Path $item.FullName -Destination (Join-Path $root $item.Name) -Recurse -Force } }; Write-Host '[THANH CONG] Da cap nhat ban moi nhat tu GitHub!' } } catch { Write-Host ('[LOI] ' + $_.Exception.Message) } finally { Remove-Item $tmpZip -Force -ErrorAction SilentlyContinue; Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue } }"

echo.
pause
endlocal
