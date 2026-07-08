$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectRoot

$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    Write-Host "找不到 .venv Python：$PythonExe" -ForegroundColor Red
    exit 1
}

& $PythonExe -m src.official_images --products data\AFC商品.csv
exit $LASTEXITCODE

