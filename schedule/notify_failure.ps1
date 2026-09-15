param(
    [Parameter(Mandatory=$true)][string]$LogPath,
    [switch]$TestNotification,
    [switch]$Resolved
)
$ErrorActionPreference = 'Stop'
$desktop = [Environment]::GetFolderPath('Desktop')
$statusLauncher = Join-Path (Split-Path -Parent $PSScriptRoot) 'weekly_status.bat'
$desktopNotice = Join-Path $desktop 'GovControl Weekly Status.txt'
if ($Resolved) {
    if (Test-Path -LiteralPath $desktopNotice) {
        "GOVCONTROL WEEKLY AUTOMATION: SUCCESS - previous failure resolved.`r`nUpdated: $(Get-Date)`r`nLog: $LogPath" |
            Set-Content -LiteralPath $desktopNotice -Encoding UTF8
    }
    exit 0
}
if (-not $TestNotification) {
    "GOVCONTROL WEEKLY AUTOMATION: FAILED - ACTION REQUIRED`r`nUpdated: $(Get-Date)`r`nLog: $LogPath`r`nOpen $statusLauncher for details. No automatic retry is scheduled." |
        Set-Content -LiteralPath $desktopNotice -Encoding UTF8
}
$noticeTitle = if ($TestNotification) { 'GovControl notification test' } else { 'GovControl weekly update/deploy FAILED' }
$noticeBody = if ($TestNotification) {
    'Test only. No update or deployment was performed.'
} else {
    'Action required. Open weekly_status.bat or the log below. No automatic retry is scheduled.'
}
try {
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
    $escapedTitle = [Security.SecurityElement]::Escape($noticeTitle)
    $escapedBody = [Security.SecurityElement]::Escape($noticeBody)
    $escapedPath = [Security.SecurityElement]::Escape($LogPath)
    $toastXml = New-Object Windows.Data.Xml.Dom.XmlDocument
    $toastXml.LoadXml("<toast duration='long'><visual><binding template='ToastGeneric'><text>$escapedTitle</text><text>$escapedBody</text><text>$escapedPath</text></binding></visual></toast>")
    $toast = [Windows.UI.Notifications.ToastNotification]::new($toastXml)
    $toast.Tag = 'GovControlWeekly'
    $toast.Group = 'GovControl'
    $powerShellApp = Get-StartApps | Where-Object { $_.Name -eq 'Windows PowerShell' } | Select-Object -First 1
    if (-not $powerShellApp) { throw 'Windows PowerShell notification identity was not found.' }
    $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($powerShellApp.AppID)
    if ([string]$notifier.Setting -ne 'Enabled') { throw "Windows notifications are disabled: $($notifier.Setting)" }
    $notifier.Show($toast)
} catch {
    # Do not change the user's notification settings. A desktop file persists
    # beyond a locked screen; a short popup makes failures visible when unlocked.
    $popupSeconds = if ($TestNotification) { 7 } else { 20 }
    $shell = New-Object -ComObject WScript.Shell
    $popupText = $noticeBody + "`r`n`r`nLog: " + $LogPath
    if (-not $TestNotification) { $popupText += "`r`nDesktop notice: " + $desktopNotice }
    $shell.Popup($popupText, $popupSeconds, $noticeTitle, 4112) | Out-Null
}
