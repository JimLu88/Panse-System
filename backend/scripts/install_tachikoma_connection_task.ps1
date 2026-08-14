$ErrorActionPreference = "Stop"

$taskName = "Tachikoma Panse System Connection"
$backendRoot = Split-Path -Parent $PSScriptRoot
$launcher = Join-Path $PSScriptRoot "start_tachikoma_connection.ps1"
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$userId = "$env:USERDOMAIN\$env:USERNAME"

$action = New-ScheduledTaskAction `
    -Execute $powershell `
    -Argument "-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$launcher`"" `
    -WorkingDirectory $backendRoot
$principal = New-ScheduledTaskPrincipal -UserId $userId -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0)

$task = New-ScheduledTask `
    -Action $action `
    -Principal $principal `
    -Settings $settings `
    -Description "On-demand hidden Panse ERP connector; identity and sanitized product/SKU reads only."

Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null
Disable-ScheduledTask -TaskName $taskName -ErrorAction Stop | Out-Null
