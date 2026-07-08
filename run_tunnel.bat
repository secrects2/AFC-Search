@echo off
REM AFC Cloudflare Tunnel
cd /d "%~dp0"
if not exist "logs" mkdir logs
echo [%date% %time%] Tunnel started >> logs\tunnel.log
cloudflared tunnel run afc-monitor >> logs\tunnel.log 2>&1
