$projectRoot = Split-Path -Parent $PSScriptRoot
$statusPath = Join-Path $projectRoot 'logs\weekly\latest-status.json'
$taskName = 'GovControl Weekly Update and Deploy'
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
    $info = Get-ScheduledTaskInfo -TaskName $taskName
    Write-Host "Schedule: Sundays 10 PM Pacific. Windows sign-in required; Codex can be closed."
    Write-Host "Task state: $($task.State)   Next run: $($info.NextRunTime)"
    Write-Host "Last task result: $($info.LastTaskResult) (0 = success; 267011 = not run yet)"
    if ($info.LastTaskResult -notin @(0, 267011, 267009)) {
        Write-Host "WINDOWS TASK FAILURE: result $($info.LastTaskResult). Review Task Scheduler and the logs." -ForegroundColor Red
    }
    if ($task.State -eq 'Disabled') { Write-Host 'WARNING: the weekly task is disabled.' -ForegroundColor Red }
} else {
    Write-Host 'NOT SCHEDULED: the Windows task is missing.' -ForegroundColor Red
}
if (Test-Path -LiteralPath $statusPath) {
    $status = Get-Content -Raw -LiteralPath $statusPath | ConvertFrom-Json
    $color = if ($status.status -eq 'success') { 'Green' } elseif ($status.status -eq 'running') { 'Yellow' } else { 'Red' }
    Write-Host ("LAST RUN: " + $status.status.ToUpper()) -ForegroundColor $color
    Write-Host "Started: $($status.started_at)   Step: $($status.step)"
    if ($status.status -eq 'running' -and (!$task -or $task.State -ne 'Running')) {
        Write-Host 'INTERRUPTED: the last run never recorded completion. Review the log.' -ForegroundColor Red
    }
    if ($status.error) { Write-Host $status.error -ForegroundColor Red }
    Write-Host "Full log: $($status.log)"
    if (Test-Path -LiteralPath $status.log) {
        Write-Host ''
        Get-Content -LiteralPath $status.log -Tail 40
    }
} else {
    Write-Host 'No weekly run has completed yet.'
}
