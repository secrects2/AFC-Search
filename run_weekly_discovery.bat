@echo off
REM AFC 每週新連結發現 — 受搜尋 API 預算控制
cd /d "%~dp0"
if not exist "logs" mkdir logs
echo [%date% %time%] 每週搜尋開始 >> logs\discovery.log
.venv\Scripts\python.exe -m src.services.discovery_search --mode weekly >> logs\discovery.log 2>&1
echo [%date% %time%] 每週搜尋結束 >> logs\discovery.log
echo. >> logs\discovery.log
