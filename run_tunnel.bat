@echo off
setlocal

REM AFC Cloudflare Tunnel
cd /d "%~dp0"
if not exist "logs" mkdir logs

set "CLOUDFLARED=C:\Program Files (x86)\cloudflared\cloudflared.exe"
if not exist "%CLOUDFLARED%" set "CLOUDFLARED=C:\Program Files\cloudflared\cloudflared.exe"
if not exist "%CLOUDFLARED%" (
    echo [%date% %time%] ERROR: cloudflared.exe not found >> logs\tunnel.log
    exit /b 1
)

echo [%date% %time%] Tunnel started >> logs\tunnel.log
"%CLOUDFLARED%" tunnel --config "%~dp0cloudflared.local.yml" run afc-monitor >> logs\tunnel.log 2>&1
set "EXIT_CODE=%ERRORLEVEL%"
echo [%date% %time%] Tunnel stopped with exit code %EXIT_CODE% >> logs\tunnel.log

endlocal & exit /b %EXIT_CODE%
