@echo off
REM AFC 每月完整補掃 — 受搜尋 API 預算控制
cd /d "%~dp0"
if not exist "logs" mkdir logs
echo [%date% %time%] 每月補掃開始 >> logs\discovery.log
.venv\Scripts\python.exe -m src.services.discovery_search --mode full >> logs\discovery.log 2>&1
echo [%date% %time%] 每月補掃結束 >> logs\discovery.log
echo. >> logs\discovery.log
