@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================================
echo   KHOI DONG CONG CU "TOM TAT - BIEN SOAN" (.DOCX)
echo ============================================================================
echo.

if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -c "import sys; sys.path.insert(0, r'%~dp0src'); from tomtat.updater import check_update; r = check_update(); print('-> [PHIEN BAN] ' + r['message'] + (' (Ban co the bam nut [Cap nhat] tren giao dien web de nang cap)' if r.get('has_update') else ''))" 2>nul
)

echo.
echo Dang mo giao dien web tai http://127.0.0.1:8765 ...
call "%~dp0tomtat.bat" review
