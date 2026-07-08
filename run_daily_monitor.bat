@echo off
REM AFC 每日監測 — 只查已知 URL，不呼叫搜尋 API
cd /d "C:\Users\secre\Documents\Codex\2026-07-07\windows\work\price-monitor"
echo [%date% %time%] 每日監測開始 >> logs\daily_monitor.log
python -m src.services.daily_monitor >> logs\daily_monitor.log 2>&1
echo [%date% %time%] 每日監測結束 >> logs\daily_monitor.log
echo. >> logs\daily_monitor.log
