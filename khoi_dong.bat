@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================================
echo   KHOI DONG CONG CU "TOM TAT - BIEN SOAN" (.DOCX)
echo ============================================================================
echo.

set "TOMTAT_REVIEW_ARGS="
set "TOMTAT_RESTART_ON_EXIT=1"

:start_loop
if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -c "import sys; sys.path.insert(0, r'%~dp0src'); from tomtat.updater import check_update; r = check_update(cache_for_startup=True, include_details=True); msg = r['message'] + (' - ' + r['latest_message'] if r.get('has_update') and r.get('latest_message') else ''); print('-> [PHIEN BAN] ' + msg + (' (Ban co the bam nut [Cap nhat] tren giao dien web de nang cap)' if r.get('has_update') else ''))" 2>nul
)

echo.
echo Dang mo giao dien web tai http://127.0.0.1:8765 ...
call "%~dp0tomtat.bat" review %TOMTAT_REVIEW_ARGS%
if errorlevel 42 if not errorlevel 43 (
    echo.
    echo -> [CAP NHAT] Dang khoi dong lai may chu de ap dung phien ban moi...
    set "TOMTAT_REVIEW_ARGS=--no-browser"
    goto :start_loop
)
