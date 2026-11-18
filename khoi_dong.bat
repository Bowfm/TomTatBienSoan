@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Dang khoi dong giao dien web tai http://127.0.0.1:8765 ...
call "%~dp0tomtat.bat" review
