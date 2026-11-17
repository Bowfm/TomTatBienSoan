@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "%~dp0cap_nhat.bat" call "%~dp0cap_nhat.bat"
echo Dang khoi dong lai giao dien web tai http://127.0.0.1:8765 ...
call "%~dp0tomtat.bat" review
