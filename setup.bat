@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"

echo ============================================================================
echo   CAI DAT TU DONG CONG CU "TOM TAT - BIEN SOAN" (.DOCX)
echo ============================================================================
echo.

:: 0. Kiem tra xem co phai dang chay ben trong file ZIP chua giai nen khong
if exist "%~dp0requirements.txt" goto :has_requirements
echo.
echo ============================================================================
echo   [CHU Y QUAN TRONG] CHUA GIAI NEN FILE ZIP!
echo ============================================================================
echo Ban dang chay truc tiep file setup.bat ben trong file nen ZIP.
echo Vui long lam theo 3 buoc sau:
echo   1. Dong cua so nay lai
echo   2. Chuot phai vao file ZIP da tai ve, chon "Extract All..."
echo   3. Mo thu muc vua giai nen ra roi moi nhap dup chay setup.bat
echo ============================================================================
echo.
pause
exit /b 1

:has_requirements
:: 1. Tim trinh thong dich Python (3.11+)
set "PY_CMD="

:: Thu moi truong ao neu da co san
if exist "%~dp0.venv\Scripts\python.exe" (
    set "PY_CMD=%~dp0.venv\Scripts\python.exe"
    goto :found_python
)

:: Thu trinh khoi chay py
where py >nul 2>nul
if errorlevel 1 goto :check_python_cmd
py -3.12 --version >nul 2>nul
if not errorlevel 1 set "PY_CMD=py -3.12" & goto :found_python
py -3.11 --version >nul 2>nul
if not errorlevel 1 set "PY_CMD=py -3.11" & goto :found_python
py -3.13 --version >nul 2>nul
if not errorlevel 1 set "PY_CMD=py -3.13" & goto :found_python
py -3 --version >nul 2>nul
if not errorlevel 1 set "PY_CMD=py -3" & goto :found_python

:check_python_cmd
:: Thu lenh python trong PATH
where python >nul 2>nul
if errorlevel 1 goto :check_default_paths
python --version >nul 2>nul
if errorlevel 1 goto :check_default_paths
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if not errorlevel 1 set "PY_CMD=python" & goto :found_python

:check_default_paths
:: Thu tim trong cac thu muc cai dat mac dinh cua Windows
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PY_CMD=%LocalAppData%\Programs\Python\Python312\python.exe" & goto :found_python
if exist "%LocalAppData%\Programs\Python\Python311\python.exe" set "PY_CMD=%LocalAppData%\Programs\Python\Python311\python.exe" & goto :found_python
if exist "%LocalAppData%\Programs\Python\Python313\python.exe" set "PY_CMD=%LocalAppData%\Programs\Python\Python313\python.exe" & goto :found_python
if exist "C:\Program Files\Python312\python.exe" set "PY_CMD=C:\Program Files\Python312\python.exe" & goto :found_python
if exist "C:\Program Files\Python311\python.exe" set "PY_CMD=C:\Program Files\Python311\python.exe" & goto :found_python
if exist "C:\Program Files\Python313\python.exe" set "PY_CMD=C:\Program Files\Python313\python.exe" & goto :found_python
if exist "C:\Python312\python.exe" set "PY_CMD=C:\Python312\python.exe" & goto :found_python
if exist "C:\Python311\python.exe" set "PY_CMD=C:\Python311\python.exe" & goto :found_python

:: Khong tim thay Python -> ho tro tu dong cai qua winget hoac huong dan tai
echo.
echo ============================================================================
echo   [CHUA CO PYTHON] May tinh cua ban chua cai dat Python 3.11+.
echo ============================================================================
where winget >nul 2>nul
if errorlevel 1 goto :no_winget
echo Dang thu tu dong cai dat Python 3.12 qua Windows Package Manager...
winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
if errorlevel 1 goto :no_winget
echo Da cai xong Python 3.12! Vui long TAT cua so nay va nhap dup lai setup.bat.
pause
exit /b 0

:no_winget
echo Vui long tai va cai dat Python 3.12 tai: https://www.python.org/downloads/windows/
echo LUU Y QUAN TRONG: Nho tick vao o "Add python.exe to PATH" khi cai dat!
start https://www.python.org/downloads/windows/
echo.
pause
exit /b 1

:found_python
echo [1/4] Da tim thay trinh chay Python:
%PY_CMD% --version

:: 2. Tao moi truong ao .venv
if exist "%~dp0.venv\Scripts\python.exe" goto :venv_ready
echo.
echo [2/4] Dang tao moi truong ao .venv...
%PY_CMD% -m venv "%~dp0.venv"
if not errorlevel 1 goto :venv_ready
echo.
echo [LOI] Khong the tao moi truong ao .venv.
echo Vui long kiem tra lai quyen ghi thu muc hoac cai lai Python.
echo.
pause
exit /b 1

:venv_ready
echo.
echo [2/4] Moi truong ao .venv da ton tai san sang.

:: 3. Cai dat thu vien
echo.
echo [3/4] Dang kiem tra va cai dat cac thu vien...
"%~dp0.venv\Scripts\python.exe" -m pip install --upgrade pip -q
"%~dp0.venv\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
if not errorlevel 1 goto :pip_ok
echo.
echo [LOI] Cai dat thu vien that bai.
echo Vui long kiem tra ket noi Internet va chay lai setup.bat.
echo.
pause
exit /b 1

:pip_ok
:: Tao cac thu muc can thiet
if not exist "%~dp0input" mkdir "%~dp0input"
if not exist "%~dp0output" mkdir "%~dp0output"
if not exist "%~dp0work" mkdir "%~dp0work"

:: 4. Kiem tra khoa API va mo Notepad neu chua co key
echo.
echo [4/4] Kiem tra cau hinh khoa Gemini API trong file api_keys.txt...

if not exist "%~dp0api_keys.txt" if exist "%~dp0api_keys.example.txt" copy /Y "%~dp0api_keys.example.txt" "%~dp0api_keys.txt" >nul
if not exist "%~dp0api_keys.txt" > "%~dp0api_keys.txt" echo # Dan cac API Key vao day - moi dong 1 key, bat dau bang AIzaSy...

"%~dp0.venv\Scripts\python.exe" -c "import sys; sys.path.insert(0, r'%~dp0src'); from tomtat.config import get_api_keys; sys.exit(0 if get_api_keys() else 1)" >nul 2>nul
if not errorlevel 1 goto :keys_already_valid

echo.
echo ============================================================================
echo   BUOC CUOI CUNG: DAN GEMINI API KEYS VAO FILE api_keys.txt
echo ============================================================================
echo Cua so Notepad se mo file "api_keys.txt" ngay bay gio.
echo Hay dan cac Gemini API Key cua ban vao day - moi dong 1 key, bat dau bang AIzaSy...
echo Ban co the dan tu 1 key den 20, 30, 40 keys tuy y de chay song song sieu nhanh!
echo Bam Ctrl+S de luu lai roi dong Notepad de tiep tuc!
echo ============================================================================
echo.
notepad "%~dp0api_keys.txt"

:keys_already_valid
"%~dp0.venv\Scripts\python.exe" -c "import sys; sys.path.insert(0, r'%~dp0src'); from tomtat.config import get_api_keys; keys = get_api_keys(); print(f'-> Da nhan dien thanh cong: {len(keys)} API keys san sang su dung!') if keys else print('-> Chu y: Chua nhap API key. Ban co the mo file api_keys.txt va them key bat cu luc nao!')" 2>nul

echo.
echo ============================================================================
echo   CAI DAT HOAN TAT THANH CONG!
echo   - De mo giao dien web lam viec : Nhap dup vao file "khoi_dong.bat"
echo   - De tat server khi nghi       : Nhap dup vao file "tat_server.bat"
echo   - De cap nhat ban moi nhat     : Nhap dup vao file "cap_nhat.bat"
echo ============================================================================
echo.
pause
endlocal
