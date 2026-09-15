param([string]$PythonPath = '')
$ErrorActionPreference = 'Stop'
if (-not $PythonPath) {
    $PythonPath = (& python -c "import sys; print(sys.executable)").Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Python could not be located. Pass -PythonPath with its absolute path.' }
}
$projectRoot = Split-Path -Parent $PSScriptRoot
$taskName = 'GovControl Weekly Update and Deploy'
if ((Get-TimeZone).Id -ne 'Pacific Standard Time') {
    throw 'This schedule expects the Windows Pacific time zone. No task was changed.'
}
$pythonWindowless = Join-Path (Split-Path -Parent $PythonPath) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonWindowless)) { throw "Python was not found: $pythonWindowless" }
$runner = Join-Path $projectRoot 'weekly_run.py'
if (-not (Test-Path -LiteralPath $runner)) { throw "Weekly runner was not found: $runner" }
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing -and -not ($existing.Actions.Arguments -like "*$runner*")) {
    throw "An unrelated task already uses '$taskName'. It was not replaced."
}
$action = New-ScheduledTaskAction -Execute $pythonWindowless -Argument ('"' + $runner + '" --scheduled') -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek Sunday -At '22:00'
# Omit an explicit UTC offset so Windows follows local Pacific daylight-saving time.
$trigger.StartBoundary = (Get-Date).Date.AddHours(22).ToString('yyyy-MM-ddTHH:mm:ss')
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 10) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$description = 'Sunday 10 PM Pacific: update/build/test all sites, back up live files, then SCP-deploy only if preceding steps pass. No automatic failure retries. Logs: ' + (Join-Path $projectRoot 'logs\weekly') + '. Runs under the signed-in Windows user; Codex is not required.'
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description $description -Force | Out-Null
$task = Get-ScheduledTask -TaskName $taskName
$info = Get-ScheduledTaskInfo -TaskName $taskName
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'logs\weekly\latest-status.json'))) {
    $initialStatus = "GOVCONTROL WEEKLY AUTOMATION: SCHEDULED`r`nNext run: $($info.NextRunTime) Pacific`r`nWeekly: Sunday 10:00 PM Pacific`r`nSequence: update/build/test, backup, deploy`r`nCodex can be closed; stay signed in to Windows.`r`nRun weekly_status.bat for task status and logs."
    $initialStatus | Set-Content -LiteralPath (Join-Path $projectRoot 'WEEKLY_STATUS.txt') -Encoding UTF8
}
[pscustomobject]@{
    TaskName = $task.TaskName
    State = $task.State
    NextRunTime = $info.NextRunTime
    User = $task.Principal.UserId
    LogonType = $task.Principal.LogonType
    TimeZone = (Get-TimeZone).Id
    Action = $task.Actions.Execute + ' ' + $task.Actions.Arguments
} | Format-List
