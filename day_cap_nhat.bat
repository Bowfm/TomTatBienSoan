@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "DEFAULT_REPO=https://github.com/Bowfm/TomTatBienSoan.git"

echo ============================================================================
echo   DAY BAN CAP NHAT LEN GITHUB: %DEFAULT_REPO%
echo ============================================================================

where git >nul 2>nul
if errorlevel 1 (
    echo [CHUA CO GIT] Dang cai dat Git tu dong qua winget...
    winget install -e --id Git.Git --accept-package-agreements --accept-source-agreements
    if errorlevel 1 (
        echo Vui long tai Git mien phi tai: https://git-scm.com/download/win
        start https://git-scm.com/download/win
        pause
        exit /b 1
    )
)

if not exist ".git" (
    echo Khoi tao kho Git local...
    git init
    git branch -M main
)

git remote get-url origin >nul 2>nul
if errorlevel 1 (
    git remote add origin "%DEFAULT_REPO%"
) else (
    git remote set-url origin "%DEFAULT_REPO%"
)
echo %DEFAULT_REPO%> ".github_repo_url"

echo.
if exist ".venv\Scripts\python.exe" (
    echo Dang kiem tra nhanh cu phap va Regression Guard truoc khi day...
    ".venv\Scripts\python.exe" .agents\verify_guard.py
    if errorlevel 1 (
        echo [LOI] Kiem tra Regression Guard that bai. Huy day len GitHub!
        pause
        exit /b 1
    )
    if exist ".venv\Scripts\pytest.exe" (
        echo Dang chay bo kiem thu tu dong ^(pytest^)...
        ".venv\Scripts\pytest.exe" -q
        if errorlevel 1 (
            echo [LOI] Co test that bai. Huy day len GitHub de tranh anh huong may khac!
            pause
            exit /b 1
        )
    )
)

echo.
echo Dang dong bo code, prompts, config.yaml va bo nho hoc tap ^(tuyet doi khong day api_keys.txt, .env hay config.local.yaml^)...
git add -u
git add pyproject.toml config.yaml .env.example .gitignore .github_repo_url requirements.txt *.bat *.md *.txt prompts src tests docs skill_ref samples
if exist ".agents" git add .agents
if exist "work\learned_memory.json" git add -f "work\learned_memory.json"

git diff --cached --quiet
if errorlevel 1 (
    git commit -m "Cap nhat cong cu TomTatBienSoan (%date% %time%)"
) else (
    echo Khong co thay doi moi can commit, kiem tra day cac commit da co len GitHub...
)
git push -u origin main

if errorlevel 1 (
    echo.
    echo [LOI] Khong day duoc len GitHub. Neu lan dau tien su dung Git tren may,
    echo cua so dang nhap GitHub se hien ra — hay dang nhap tai khoan Bowfm cua ban.
) else (
    echo.
    echo [THANH CONG] Da day ban cap nhat moi nhat len:
    echo   %DEFAULT_REPO%
    echo Moi nguoi chi can mo "khoi_dong.bat" hoac bam nut "Cap nhat" tren web la nhan duoc ngay!
)
pause
endlocal
