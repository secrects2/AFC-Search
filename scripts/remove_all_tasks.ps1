$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# ---------------------------------------------------------------
# 移除所有 AFC 排程工作
# ---------------------------------------------------------------

$tasks = Get-ScheduledTask -TaskName "AFC Price Monitor*" -ErrorAction SilentlyContinue

if ($tasks) {
    foreach ($task in $tasks) {
        Unregister-ScheduledTask -TaskName $task.TaskName -Confirm:$false
        Write-Host "已移除：$($task.TaskName)" -ForegroundColor Yellow
    }
    Write-Host ""
    Write-Host "所有 AFC 排程已移除。" -ForegroundColor Green
} else {
    Write-Host "沒有找到 AFC 排程工作。" -ForegroundColor Gray
}
