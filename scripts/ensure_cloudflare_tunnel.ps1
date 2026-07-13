$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$LogDir = Join-Path $ProjectRoot "logs"
$LogPath = Join-Path $LogDir "tunnel_health.log"
$ConfigPath = Join-Path $ProjectRoot "cloudflared.local.yml"
$TunnelBatchPath = Join-Path $ProjectRoot "run_tunnel.bat"
$TunnelName = "afc-monitor"
$ExpectedOrigin = "http://127.0.0.1:8002"

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

function Write-HealthLog([string]$Message) {
    Add-Content -Path $LogPath -Value ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message) -Encoding UTF8
}

try {
    $CloudflaredPath = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
    if (-not (Test-Path $CloudflaredPath)) {
        $CloudflaredPath = "C:\Program Files\cloudflared\cloudflared.exe"
    }
    if (-not (Test-Path $CloudflaredPath)) {
        $command = Get-Command cloudflared -ErrorAction SilentlyContinue
        if ($command) {
            $CloudflaredPath = $command.Source
        }
    }
    if (-not (Test-Path $CloudflaredPath)) {
        throw "cloudflared.exe not found"
    }

    if (-not (Test-Path $ConfigPath)) {
        throw "Cloudflare config not found: $ConfigPath"
    }

    $config = Get-Content -Raw -Encoding UTF8 $ConfigPath
    if ($config -notmatch [regex]::Escape($ExpectedOrigin)) {
        throw "Tunnel origin is not $ExpectedOrigin; fix config.yml first"
    }

    try {
        $origin = Invoke-WebRequest -Uri "$ExpectedOrigin/products" -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
        if ($origin.StatusCode -ne 200) {
            throw "Dashboard returned HTTP $($origin.StatusCode)"
        }
    } catch {
        throw "Dashboard is unreachable: $($_.Exception.Message)"
    }

    $matchingProcesses = @(Get-CimInstance Win32_Process -Filter "Name = 'cloudflared.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "\btunnel\b.*\brun\s+$TunnelName\b" })

    if ($matchingProcesses.Count -eq 0) {
        Start-Process -FilePath $TunnelBatchPath -WorkingDirectory $ProjectRoot -WindowStyle Hidden | Out-Null
        Write-HealthLog "RECOVERED: no local cloudflared process; started tunnel $TunnelName."
        exit 0
    }

    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $info = (& $CloudflaredPath tunnel info $TunnelName 2>&1 | Out-String)
    $ErrorActionPreference = $previousErrorActionPreference
    $hasActiveConnection = $info -match "CONNECTOR ID" -and $info -notmatch "does not have any active connection"
    if ($hasActiveConnection) {
        Write-HealthLog "OK: Tunnel $TunnelName is connected."
        exit 0
    }

    foreach ($process in $matchingProcesses) {
        Write-HealthLog "WARN: stopping disconnected cloudflared process $($process.ProcessId)."
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }

    Start-Sleep -Seconds 2
    Start-Process -FilePath $TunnelBatchPath -WorkingDirectory $ProjectRoot -WindowStyle Hidden | Out-Null
    Write-HealthLog "RECOVERED: started cloudflared for tunnel $TunnelName."
} catch {
    Write-HealthLog "ERROR: $($_.Exception.Message)"
    exit 1
}
