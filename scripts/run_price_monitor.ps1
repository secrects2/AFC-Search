$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectRoot

$LogDir = Join-Path $ProjectRoot "logs"
$LogFile = Join-Path $LogDir "scheduler.log"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-SchedulerLog {
    param([string]$Message)
    $Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -Path $LogFile -Encoding UTF8 -Value "[$Timestamp] $Message"
}

Write-SchedulerLog "START: AFC Price Monitor"

$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    Write-SchedulerLog "ERROR: .venv Python not found: $PythonExe"
    exit 1
}

$Arguments = @("main.py", "--products", "data\AFC商品.csv", "--scheduled")
$ManualLinks = Join-Path $ProjectRoot "data\manual_links.csv"
if (Test-Path $ManualLinks) {
    $Arguments += @("--manual-links", "data\manual_links.csv")
}

try {
    & $PythonExe @Arguments *>> $LogFile
    $ExitCode = $LASTEXITCODE
    if ($ExitCode -ne 0) {
        Write-SchedulerLog "ERROR: price monitor failed with code $ExitCode"
        exit $ExitCode
    }
    Write-SchedulerLog "SUCCESS: price monitor completed"
    exit 0
}
catch {
    Write-SchedulerLog "ERROR: $($_.Exception.Message)"
    exit 1
}

