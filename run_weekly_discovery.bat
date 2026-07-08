@echo off
REM AFC 每週新連結發現 — 受搜尋 API 預算控制
cd /d "C:\Users\secre\Documents\Codex\2026-07-07\windows\work\price-monitor"
echo [%date% %time%] 每週搜尋開始 >> logs\discovery.log
python -m src.services.discovery_search --mode weekly >> logs\discovery.log 2>&1
echo [%date% %time%] 每週搜尋結束 >> logs\discovery.log
echo. >> logs\discovery.log
