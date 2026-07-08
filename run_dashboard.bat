@echo off
REM AFC Dashboard — 啟動 Web 儀表板
cd /d "%~dp0"
if not exist "logs" mkdir logs
echo [%date% %time%] Dashboard 啟動 >> logs\dashboard.log
.venv\Scripts\python.exe dashboard.py --host 127.0.0.1 --port 8001 >> logs\dashboard.log 2>&1
