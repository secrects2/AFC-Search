@echo off
REM AFC 每月完整補掃 — 受搜尋 API 預算控制
cd /d "C:\Users\secre\Documents\Codex\2026-07-07\windows\work\price-monitor"
echo [%date% %time%] 每月補掃開始 >> logs\discovery.log
python -m src.services.discovery_search --mode full >> logs\discovery.log 2>&1
echo [%date% %time%] 每月補掃結束 >> logs\discovery.log
echo. >> logs\discovery.log
