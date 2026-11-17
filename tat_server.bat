@echo off
chcp 65001 >nul
echo Dang tat server giao dien web tren cong 8765...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8765" ^| findstr "LISTENING"') do (
    echo Dang dong tien trinh PID %%a ...
    taskkill /F /T /PID %%a >nul 2>nul
)
echo Da tat server http://127.0.0.1:8765 thanh cong!
timeout /t 2 >nul
