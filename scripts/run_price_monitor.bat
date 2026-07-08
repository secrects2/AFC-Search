@echo off
setlocal

REM Always run from the project root, even when called by Windows Task Scheduler.
cd /d "%~dp0.."

if not exist logs mkdir logs

echo [%date% %time%] START: AFC Price Monitor >> logs\scheduler.log

if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
) else (
    echo [%date% %time%] ERROR: .venv not found >> logs\scheduler.log
    exit /b 1
)

set MANUAL_ARGS=
if exist "data\manual_links.csv" (
    set MANUAL_ARGS=--manual-links data\manual_links.csv
)

python main.py --products data\AFC商品.csv %MANUAL_ARGS% --scheduled >> logs\scheduler.log 2>&1

if %errorlevel% neq 0 (
    echo [%date% %time%] ERROR: price monitor failed with code %errorlevel% >> logs\scheduler.log
    exit /b %errorlevel%
) else (
    echo [%date% %time%] SUCCESS: price monitor completed >> logs\scheduler.log
)

endlocal

