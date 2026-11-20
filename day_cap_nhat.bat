@echo off
chcp 65001 >nul
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "DEFAULT_REPO=https://github.com/Bowfm/TomTatBienSoan.git"
if defined TOMTAT_PUSH_REPO set "DEFAULT_REPO=%TOMTAT_PUSH_REPO%"
set "NO_PAUSE=0"
set "CUSTOM_MSG="

:parse_args
if "%~1"=="" goto :args_done
if /I "%~1"=="--no-pause" goto :flag_no_pause
if defined CUSTOM_MSG goto :append_custom_msg
set "CUSTOM_MSG=%~1"
shift
goto :parse_args

:flag_no_pause
set "NO_PAUSE=1"
shift
goto :parse_args

:append_custom_msg
set "CUSTOM_MSG=!CUSTOM_MSG! %~1"
shift
goto :parse_args

:args_done
echo ============================================================================
echo   DAY BAN CAP NHAT LEN GITHUB: %DEFAULT_REPO%
echo ============================================================================

:: 1. Kiem tra Git (va thu tim duong dan mac dinh neu vua cai xong)
where git >nul 2>nul
if not errorlevel 1 goto :git_ready
if exist "%ProgramFiles%\Git\cmd\git.exe" set "PATH=%ProgramFiles%\Git\cmd;%PATH%" & goto :git_ready
if exist "%LocalAppData%\Programs\Git\cmd\git.exe" set "PATH=%LocalAppData%\Programs\Git\cmd;%PATH%" & goto :git_ready

echo [CHUA CO GIT] Dang cai dat Git tu dong qua winget...
winget install -e --id Git.Git --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto :git_missing
if exist "%ProgramFiles%\Git\cmd\git.exe" set "PATH=%ProgramFiles%\Git\cmd;%PATH%"
if exist "%LocalAppData%\Programs\Git\cmd\git.exe" set "PATH=%LocalAppData%\Programs\Git\cmd;%PATH%"
where git >nul 2>nul
if not errorlevel 1 goto :git_ready

:git_missing
echo [LOI] Chua tim thay Git. Vui long tai Git mien phi tai: https://git-scm.com/download/win
echo Neu vua cai xong, hay dong cua so nay va mo lai day_cap_nhat.bat.
start https://git-scm.com/download/win
goto :fail_exit

:git_ready
:: 2. Khoi tao kho Git / dam bao dung nhanh main / remote origin / tac gia
if exist ".git" goto :check_branch
echo Khoi tao kho Git local...
git init
if errorlevel 1 goto :fail_exit
git branch -M main

:check_branch
set "CUR_BRANCH="
for /f "delims=" %%B in ('git symbolic-ref --short HEAD 2^>nul') do set "CUR_BRANCH=%%B"
if /I "%CUR_BRANCH%"=="main" goto :branch_ok
echo [CHU Y] Dang o nhanh "%CUR_BRANCH%" ^(hoac detached HEAD^), dang chuyen ve nhanh main...
git show-ref --verify --quiet refs/heads/main
if errorlevel 1 (
    git checkout -b main
) else (
    git checkout main
)
if errorlevel 1 (
    echo [LOI] Khong chuyen duoc ve nhanh main. Hay kiem tra trang thai Git truoc khi day!
    goto :fail_exit
)

:branch_ok
git remote get-url origin >nul 2>nul
if errorlevel 1 (
    git remote add origin "%DEFAULT_REPO%"
) else (
    git remote set-url origin "%DEFAULT_REPO%"
)

:: Chi ghi .github_repo_url khi chua ton tai hoac noi dung khac DEFAULT_REPO
set "NEED_WRITE_REPO_URL=1"
if exist ".github_repo_url" (
    set "EXISTING_URL="
    set /p EXISTING_URL=<".github_repo_url"
    if "!EXISTING_URL!"=="%DEFAULT_REPO%" set "NEED_WRITE_REPO_URL=0"
)
if "!NEED_WRITE_REPO_URL!"=="1" (
    > ".github_repo_url" echo %DEFAULT_REPO%
)

:: Dam bao co user.name / user.email de git commit khong bao loi tren may moi
set "GIT_UNAME="
for /f "delims=" %%U in ('git config user.name 2^>nul') do set "GIT_UNAME=%%U"
if not defined GIT_UNAME git config user.name "Bowfm"
set "GIT_UEMAIL="
for /f "delims=" %%E in ('git config user.email 2^>nul') do set "GIT_UEMAIL=%%E"
if not defined GIT_UEMAIL git config user.email "bowfm@users.noreply.github.com"

:: 3. Kiem tra cu phap, Regression Guard va toan bo pytest truoc khi day
echo.
if not exist ".venv\Scripts\python.exe" goto :skip_venv_checks

echo Dang kiem tra nhanh cu phap va Regression Guard truoc khi day...
".venv\Scripts\python.exe" .agents\verify_guard.py
if errorlevel 1 (
    echo [LOI] Kiem tra Regression Guard that bai. Huy day len GitHub!
    goto :fail_exit
)

if not exist ".venv\Scripts\pytest.exe" goto :skip_venv_checks
echo Dang chay bo kiem thu tu dong ^(pytest^)...
".venv\Scripts\pytest.exe" -q
if errorlevel 1 (
    echo [LOI] Co test that bai. Huy day len GitHub de tranh anh huong may khac!
    goto :fail_exit
)
goto :after_checks

:skip_venv_checks
echo [CANH BAO] Khong thay .venv\Scripts\python.exe hoac pytest.exe, bo qua kiem thu tu dong.

:after_checks
:: 4. Dong bo ma nguon, cau hinh, prompts, bo nho hoc tap (tuyet doi khong day bi mat)
echo.
echo Dang dong bo code, prompts, config.yaml va bo nho hoc tap ^(tuyet doi khong day api_keys.txt, .env hay config.local.yaml^)...
git add -u

for %%P in (
    pyproject.toml
    config.yaml
    version.json
    .env.example
    .gitignore
    .github_repo_url
    requirements.txt
    api_keys.example.txt
    HUONG_DAN_CAI_DAT_NHANH.txt
    AGENTS.md
    README.md
    prompts
    src
    tests
    docs
    skill_ref
    samples
    .agents
    .github
    "input\.gitkeep"
    "output\.gitkeep"
    "work\.gitkeep"
) do (
    if exist %%P git add -- %%P
)
if exist "*.bat" git add -- *.bat
if exist "*.md" git add -- *.md
if exist "work\learned_memory.json" git add -f -- "work\learned_memory.json"

:: Khoa an toan kep: go bo moi file bi mat/ca nhan neu lo bi stage
git rev-parse --verify --quiet HEAD >nul 2>nul
if not errorlevel 1 (
    git reset -q HEAD -- api_keys.txt .env config.local.yaml "work\quota_exhausted.json" "work\.startup_update_cache.json" "work\.updater_manifest.json" 2>nul
)
if not exist ".venv\Scripts\python.exe" goto :skip_staged_helper
if not exist ".agents\push_helper.py" goto :skip_staged_helper
".venv\Scripts\python.exe" .agents\push_helper.py check-staged
if errorlevel 1 (
    echo [LOI] Phat hien file nhay cam hoac du lieu ca nhan dang duoc stage. Huy day len GitHub!
    goto :fail_exit
)
:skip_staged_helper

:: 5. Tao commit neu co thay doi moi
git diff --cached --quiet
if not errorlevel 1 (
    echo Khong co thay doi moi can commit, kiem tra day cac commit da co len GitHub...
    goto :sync_remote
)

set "MSG_FILE=%TEMP%\tomtat_commit_msg_%RANDOM%.txt"
if exist "%MSG_FILE%" del /f /q "%MSG_FILE%" >nul 2>nul

if defined CUSTOM_MSG goto :write_custom_msg
if not exist ".venv\Scripts\python.exe" goto :write_fallback_msg
if not exist ".agents\push_helper.py" goto :write_fallback_msg
".venv\Scripts\python.exe" .agents\push_helper.py commit-message --output "%MSG_FILE%"
goto :msg_ready

:write_custom_msg
> "%MSG_FILE%" echo !CUSTOM_MSG!
goto :msg_ready

:write_fallback_msg
> "%MSG_FILE%" echo Cap nhat cong cu TomTatBienSoan ^(%date% %time%^)

:msg_ready
if not exist "%MSG_FILE%" > "%MSG_FILE%" echo Cap nhat cong cu TomTatBienSoan ^(%date% %time%^)

git commit -F "%MSG_FILE%"
set "COMMIT_ERR=%errorlevel%"
if exist "%MSG_FILE%" del /f /q "%MSG_FILE%" >nul 2>nul
if not "%COMMIT_ERR%"=="0" (
    echo [LOI] Tao commit Git that bai!
    goto :fail_exit
)

:sync_remote
:: 6. Dong bo voi origin/main truoc khi push (tranh loi rejected khi GitHub co commit moi hon)
echo.
echo Dang kiem tra va dong bo voi GitHub ^(origin/main^)...
git fetch origin main
if errorlevel 1 (
    echo [CANH BAO] Khong fetch duoc tu GitHub ^(se thu push truc tiep^)...
    goto :do_push
)

set "REMOTE_AHEAD=0"
for /f "delims=" %%C in ('git rev-list --count HEAD..origin/main 2^>nul') do set "REMOTE_AHEAD=%%C"
if "%REMOTE_AHEAD%"=="0" goto :do_push

echo [DONG BO] GitHub dang co %REMOTE_AHEAD% commit moi hon, dang gop ^(pull --rebase^)...
git pull --rebase --autostash origin main
if errorlevel 1 (
    git rebase --abort >nul 2>nul
    echo [LOI] Xung dot khi gop voi ban tren GitHub! Vui long kiem tra lai truoc khi day.
    goto :fail_exit
)

:do_push
set "LOCAL_AHEAD=0"
for /f "delims=" %%C in ('git rev-list --count origin/main..HEAD 2^>nul') do set "LOCAL_AHEAD=%%C"
git rev-parse --verify --quiet origin/main >nul 2>nul
if errorlevel 1 set "LOCAL_AHEAD=1"

if "%LOCAL_AHEAD%"=="0" (
    for /f "delims=" %%H in ('git rev-parse --short HEAD 2^>nul') do set "HEAD_SHORT=%%H"
    echo.
    echo [DA DONG BO] GitHub da o phien ban moi nhat ^(!HEAD_SHORT!^). Khong co gi moi de day!
    goto :success_exit
)

echo Dang day ban cap nhat len GitHub...
git push -u origin main
if errorlevel 1 (
    echo.
    echo [LOI] Khong day duoc len GitHub. Neu lan dau tien su dung Git tren may,
    echo cua so dang nhap GitHub se hien ra - hay dang nhap tai khoan Bowfm cua ban.
    goto :fail_exit
)

for /f "delims=" %%H in ('git rev-parse --short HEAD 2^>nul') do set "HEAD_SHORT=%%H"
set "HEAD_SUBJ="
for /f "delims=" %%S in ('git log -1 --pretty^=%%s 2^>nul') do set "HEAD_SUBJ=%%S"
echo.
echo [THANH CONG] Da day ban cap nhat moi nhat len:
echo   Repo     : %DEFAULT_REPO%
echo   Phien ban: !HEAD_SHORT! - !HEAD_SUBJ!
echo Moi nguoi chi can mo "khoi_dong.bat", chay "cap_nhat.bat" hoac bam nut "Cap nhat" tren web la nhan duoc ngay!

:success_exit
if "%NO_PAUSE%"=="0" pause
endlocal
exit /b 0

:fail_exit
if "%NO_PAUSE%"=="0" pause
endlocal
exit /b 1
