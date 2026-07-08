$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectRoot

$LogDir = Join-Path $ProjectRoot "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    Write-Host "找不到 .venv Python：$PythonExe" -ForegroundColor Red
    Write-Host "請先執行：python -m venv .venv，並安裝 requirements.txt" -ForegroundColor Yellow
    exit 1
}

Write-Host "AFC 價格監控本機網站：http://127.0.0.1:8001" -ForegroundColor Green
& $PythonExe dashboard.py --host 127.0.0.1 --port 8001
