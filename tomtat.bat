@echo off
chcp 65001 >nul
setlocal
set "TOMTAT_HOME=%~dp0"
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

if not exist "%~dp0.venv\Scripts\python.exe" (
    echo [LOI] Chua co moi truong ao .venv. Hay chay setup.bat truoc.
    exit /b 1
)

"%~dp0.venv\Scripts\python.exe" -m tomtat %*
exit /b %errorlevel%
