@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================================================
echo   CAI DAT TU DONG CONG CU "TOM TAT - BIEN SOAN" (.DOCX)
echo ============================================================================
echo.

set "PY_CMD="
where py >nul 2>nul
if %errorlevel%==0 (
    py -3.12 --version >nul 2>nul
    if not errorlevel 1 (
        set "PY_CMD=py -3.12"
    ) else (
        py -3.11 --version >nul 2>nul
        if not errorlevel 1 (
            set "PY_CMD=py -3.11"
        ) else (
            py -3 --version >nul 2>nul
            if not errorlevel 1 set "PY_CMD=py -3"
        )
    )
)

if "%PY_CMD%"=="" (
    where python >nul 2>nul
    if not errorlevel 1 set "PY_CMD=python"
)

if "%PY_CMD%"=="" (
    echo [CHUA CO PYTHON] May tinh cua ban chua cai dat Python 3.11+.
    where winget >nul 2>nul
    if not errorlevel 1 (
        echo Dang tu dong cai dat Python 3.12 qua Windows Package Manager ^(winget^)...
        winget install -e --id Python.Python.3.12 --scope machine --accept-package-agreements --accept-source-agreements
        if not errorlevel 1 (
            echo Da cai xong Python 3.12! Vui long TAT cua so nay va nhap dup lai setup.bat.
            pause
            exit /b 0
        )
    )
    echo Vui long tai va cai Python 3.12 tai: https://www.python.org/downloads/windows/
    echo ^(Luu y QUAN TRONG: Nho tick vao o "Add python.exe to PATH" khi cai dat!^)
    start https://www.python.org/downloads/windows/
    pause
    exit /b 1
)

echo [1/4] Da tim thay Python:
%PY_CMD% --version

if not exist ".venv\Scripts\python.exe" (
    echo.
    echo [2/4] Dang tao moi truong ao .venv...
    %PY_CMD% -m venv .venv
    if errorlevel 1 (
        echo [LOI] Khong tao duoc moi truong ao .venv.
        pause
        exit /b 1
    )
) else (
    echo.
    echo [2/4] Moi truong ao .venv da ton tai.
)

echo.
echo [3/4] Dang tai va cai dat cac thu vien ^(google-genai, python-docx, flask, openpyxl...^)...
".venv\Scripts\python.exe" -m pip install --upgrade pip -q
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [LOI] Cai dat thu vien that bai. Vui long kiem tra ket noi Internet va chay lai setup.bat.
    pause
    exit /b 1
)

if not exist "input" mkdir "input"
if not exist "output" mkdir "output"
if not exist "work" mkdir "work"

echo.
echo [4/4] Kiem tra cau hinh khoa GEMINI_API_KEY trong file .env...
if not exist ".env" (
    copy /Y ".env.example" ".env" >nul
)

findstr /C:"your_gemini_api_key_here" ".env" >nul 2>nul
if not errorlevel 1 (
    echo.
    echo ============================================================================
    echo   BUOC CUOI CUNG: DIEN KHOA GEMINI_API_KEY VAO FILE .env
    echo ============================================================================
    echo Cua so Notepad se mo file ".env" ngay bay gio.
    echo Hay thay chu "your_gemini_api_key_here" bang API Key cua ban, bam Ctrl+S luu lai
    echo roi dong Notepad de hoan tat!
    notepad ".env"
)

echo.
echo ============================================================================
echo   CAI DAT HOAN TAT THANH CONG!
echo   - De mo giao dien web lam viec: Nhap dup vao file "khoi_dong.bat"
echo   - De tat server khi nghi      : Nhap dup vao file "tat_server.bat"
echo ============================================================================
pause
endlocal
