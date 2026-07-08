$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$TaskName = "AFC Price Monitor"

try {
    $ExistingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $ExistingTask) {
        Write-Host "找不到 Windows 工作排程：$TaskName"
        exit 0
    }

    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "已移除 Windows 工作排程：$TaskName" -ForegroundColor Green
}
catch {
    Write-Host "移除 Windows 工作排程失敗：$($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

