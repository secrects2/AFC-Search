@echo off
setlocal

cd /d "%~dp0.."

if not exist logs mkdir logs

if exist ".venv\Scripts\python.exe" (
    echo AFC 價格監控本機網站：http://127.0.0.1:8002
    ".venv\Scripts\python.exe" dashboard.py --host 127.0.0.1 --port 8002
) else (
    echo ERROR: .venv Python not found.
    echo Please run: python -m venv .venv
    exit /b 1
)

endlocal
