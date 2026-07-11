<#
.SYNOPSIS
    AFC Price Monitor - Windows Scheduled Tasks Setup
.DESCRIPTION
    Sets up: Dashboard auto-start, daily monitor, weekly discovery, monthly full scan
#>

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Write-Host ""
Write-Host "======================================="
Write-Host "  AFC Price Monitor - Setup All Tasks"
Write-Host "======================================="
Write-Host "Project root: $ProjectRoot"
Write-Host ""

# Ensure logs directory
$logsDir = Join-Path $ProjectRoot "logs"
if (-not (Test-Path $logsDir)) {
    New-Item -ItemType Directory -Path $logsDir -Force | Out-Null
}

$TaskPrefix = "AFC Price Monitor"
$UserPrincipal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

$DefaultSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5)

# ---------------------------------------------------------------
# 1. Dashboard - auto-start on logon
# ---------------------------------------------------------------
Write-Host "[1/4] Dashboard auto-start on logon..."

$dashAction = New-ScheduledTaskAction `
    -Execute (Join-Path $ProjectRoot "run_dashboard.bat") `
    -WorkingDirectory $ProjectRoot

$dashTrigger = New-ScheduledTaskTrigger -AtLogOn

$dashSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Days 365) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName "$TaskPrefix - Dashboard" `
    -Action $dashAction `
    -Trigger $dashTrigger `
    -Settings $dashSettings `
    -Principal $UserPrincipal `
    -Description "AFC Dashboard (http://127.0.0.1:8002)" `
    -Force | Out-Null

Write-Host "  OK - Dashboard will start on logon (port 8002)" -ForegroundColor Green

# ---------------------------------------------------------------
# 2. Daily Monitor - every day at 08:00
# ---------------------------------------------------------------
Write-Host "[2/4] Daily monitor at 08:00..."

$dailyAction = New-ScheduledTaskAction `
    -Execute (Join-Path $ProjectRoot "run_daily_monitor.bat") `
    -WorkingDirectory $ProjectRoot

$dailyTrigger = New-ScheduledTaskTrigger -Daily -At 8:00AM

Register-ScheduledTask `
    -TaskName "$TaskPrefix - Daily Monitor" `
    -Action $dailyAction `
    -Trigger $dailyTrigger `
    -Settings $DefaultSettings `
    -Principal $UserPrincipal `
    -Description "AFC Daily price check for known URLs" `
    -Force | Out-Null

Write-Host "  OK - Daily monitor at 08:00" -ForegroundColor Green

# ---------------------------------------------------------------
# 3. Weekly Discovery - Monday 09:00
# ---------------------------------------------------------------
Write-Host "[3/4] Weekly discovery on Monday 09:00..."

$weeklyAction = New-ScheduledTaskAction `
    -Execute (Join-Path $ProjectRoot "run_weekly_discovery.bat") `
    -WorkingDirectory $ProjectRoot

$weeklyTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 9:00AM

Register-ScheduledTask `
    -TaskName "$TaskPrefix - Weekly Discovery" `
    -Action $weeklyAction `
    -Trigger $weeklyTrigger `
    -Settings $DefaultSettings `
    -Principal $UserPrincipal `
    -Description "AFC Weekly new URL discovery via search API" `
    -Force | Out-Null

Write-Host "  OK - Weekly discovery on Monday 09:00" -ForegroundColor Green

# ---------------------------------------------------------------
# 4. Monthly Full Scan - every 4 weeks on Sunday 10:00
# ---------------------------------------------------------------
Write-Host "[4/4] Monthly full scan..."

$monthlyAction = New-ScheduledTaskAction `
    -Execute (Join-Path $ProjectRoot "run_monthly_full_discovery.bat") `
    -WorkingDirectory $ProjectRoot

$monthlyTrigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -WeeksInterval 4 -At 10:00AM

Register-ScheduledTask `
    -TaskName "$TaskPrefix - Monthly Full Scan" `
    -Action $monthlyAction `
    -Trigger $monthlyTrigger `
    -Settings $DefaultSettings `
    -Principal $UserPrincipal `
    -Description "AFC Monthly full product scan" `
    -Force | Out-Null

Write-Host "  OK - Monthly full scan every 4 weeks on Sunday 10:00" -ForegroundColor Green

# ---------------------------------------------------------------
# Summary
# ---------------------------------------------------------------
Write-Host ""
Write-Host "======================================="
Write-Host "  All tasks created successfully!"
Write-Host "======================================="
Write-Host ""
Write-Host "  [v] Dashboard       -> auto-start on logon" -ForegroundColor Green
Write-Host "  [v] Daily Monitor   -> every day 08:00" -ForegroundColor Green
Write-Host "  [v] Weekly Discovery-> Monday 09:00" -ForegroundColor Green
Write-Host "  [v] Monthly Scan    -> every 4 weeks Sunday 10:00" -ForegroundColor Green
Write-Host ""
Write-Host "Manage tasks:"
Write-Host "  View:   Get-ScheduledTask -TaskName 'AFC*' | Format-Table TaskName, State"
Write-Host "  Remove: .\scripts\remove_all_tasks.ps1"
Write-Host ""
Write-Host "Next: To let colleagues access, run:"
Write-Host "  .\scripts\setup_cloudflare_tunnel.ps1"
Write-Host ""
