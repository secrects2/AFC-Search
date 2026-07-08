@echo off
REM AFC 價格監控 - Windows 排程器專用
REM 每天早上9點和晚上9點各執行一次

cd /d "C:\Users\secre\Documents\Codex\2026-07-07\windows\work\price-monitor"

echo [%date% %time%] 開始執行價格監控... >> logs\scheduled_runs.log

python main.py --scheduled >> logs\scheduled_runs.log 2>&1

echo [%date% %time%] 執行完畢 >> logs\scheduled_runs.log
echo. >> logs\scheduled_runs.log
