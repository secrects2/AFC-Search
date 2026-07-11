$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$TaskPrefix = "AFC Price Monitor"
$TunnelTaskName = "$TaskPrefix - Cloudflare Tunnel"
$HealthTaskName = "$TaskPrefix - Cloudflare Tunnel Health"
$BatchPath = Join-Path $ProjectRoot "run_tunnel.bat"
$HealthScriptPath = Join-Path $ProjectRoot "scripts\ensure_cloudflare_tunnel.ps1"
$UserId = "$env:USERDOMAIN\$env:USERNAME"

if (-not (Test-Path $BatchPath)) {
    throw "Tunnel launcher not found: $BatchPath"
}
if (-not (Test-Path $HealthScriptPath)) {
    throw "Health script not found: $HealthScriptPath"
}

$Principal = New-ScheduledTaskPrincipal `
    -UserId $UserId `
    -LogonType Interactive `
    -RunLevel Limited

$TunnelAction = New-ScheduledTaskAction `
    -Execute $BatchPath `
    -WorkingDirectory $ProjectRoot

$TunnelTrigger = New-ScheduledTaskTrigger -AtLogOn
$TunnelSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Days 365) `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 1)

$existingTunnelTask = Get-ScheduledTask -TaskName $TunnelTaskName -ErrorAction SilentlyContinue
if ($existingTunnelTask) {
    Write-Host "Existing Tunnel task found; using the updated run_tunnel.bat." -ForegroundColor Gray
} else {
    Register-ScheduledTask `
        -TaskName $TunnelTaskName `
        -Action $TunnelAction `
        -Trigger $TunnelTrigger `
        -Settings $TunnelSettings `
        -Principal $Principal `
        -Description "AFC Cloudflare Tunnel persistent connector" `
        -Force | Out-Null
}

$HealthAction = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$HealthScriptPath`"" `
    -WorkingDirectory $ProjectRoot

$HealthTrigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 5) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$HealthSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName $HealthTaskName `
    -Action $HealthAction `
    -Trigger $HealthTrigger `
    -Settings $HealthSettings `
    -Principal $Principal `
    -Description "AFC Cloudflare Tunnel health check and automatic recovery" `
    -Force | Out-Null

Write-Host "Cloudflare Tunnel recovery tasks are ready:" -ForegroundColor Green
Write-Host "  $TunnelTaskName  -> start at logon" -ForegroundColor Gray
Write-Host "  $HealthTaskName  -> check and recover every 5 minutes" -ForegroundColor Gray
