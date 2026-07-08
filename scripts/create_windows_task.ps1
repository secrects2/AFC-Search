$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$TaskName = "AFC Price Monitor"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ScriptPath = Join-Path $ProjectRoot "scripts\run_price_monitor.ps1"

if (-not (Test-Path $ScriptPath)) {
    Write-Host "找不到排程執行腳本：$ScriptPath" -ForegroundColor Red
    exit 1
}

try {
    $Action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`"" `
        -WorkingDirectory $ProjectRoot

    $Trigger = New-ScheduledTaskTrigger -Daily -At 8:00AM

    $Settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable

    $Principal = New-ScheduledTaskPrincipal `
        -UserId "$env:USERDOMAIN\$env:USERNAME" `
        -LogonType Interactive `
        -RunLevel LeastPrivilege

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $Action `
        -Trigger $Trigger `
        -Settings $Settings `
        -Principal $Principal `
        -Description "定期監控 AFC 商品在電商平台是否有疑似破價商品" `
        -Force | Out-Null

    Write-Host "已建立 Windows 工作排程：$TaskName" -ForegroundColor Green
    Write-Host "專案根目錄：$ProjectRoot"
    Write-Host "排程時間：每日 08:00"
}
catch {
    Write-Host "建立 Windows 工作排程失敗：$($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

