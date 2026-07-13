#requires -RunAsAdministrator
$ErrorActionPreference = "Stop"

$trigger = New-ScheduledTaskTrigger `
    -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday `
    -At 8:00AM

$taskNames = @(
    "AFC Price Monitor - Daily Monitor",
    ("AFC" + [char]0x6BCF + [char]0x65E5 + [char]0x76E3 + [char]0x6E2C)
)

foreach ($taskName in $taskNames) {
    $task = Get-ScheduledTask -TaskName $taskName -TaskPath "\" -ErrorAction SilentlyContinue
    if ($task) {
        Set-ScheduledTask -TaskName $taskName -TaskPath "\" -Trigger $trigger | Out-Null
        Write-Host "Updated: $taskName"
    } else {
        Write-Host "Not found: $taskName"
    }
}

Write-Host "Daily monitor schedule is Monday-Friday at 08:00."
