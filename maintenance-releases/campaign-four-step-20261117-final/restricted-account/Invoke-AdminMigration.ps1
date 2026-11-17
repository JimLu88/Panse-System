$ErrorActionPreference='Stop'
$Stage='C:\ProgramData\PanseCampaignExec'
$Python='D:\AI\畔色ERP系统\Web-Agent程序\.venv\Scripts\python.exe'
if ((Test-Path -LiteralPath (Join-Path $Stage 'migration-receipt.json')) -and (Test-Path -LiteralPath (Join-Path $Stage 'broker-verification.json'))) {
    Write-Output 'Already verified; account, passwords, ACLs and broker task were not changed.'
    exit 0
}
try {
    $Script=Join-Path $PSScriptRoot 'install_restricted_runtime.py'
    # Resume only our own stalled migration helper, never a business service.
    $Old=@(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | Where-Object { $_.CommandLine -and $_.CommandLine.Contains($Script) -and $_.CommandLine.Contains('--apply') })
    foreach ($ProcessToStop in $Old) { Stop-Process -Id $ProcessToStop.ProcessId -ErrorAction Stop }
    $Process=Start-Process -FilePath $Python -ArgumentList @('-u',('"'+$Script+'"'),'--apply') -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput (Join-Path $PSScriptRoot 'migration-admin.log') -RedirectStandardError (Join-Path $PSScriptRoot 'migration-admin.stderr.log')
    if ($Process.ExitCode -ne 0) { throw 'Migration failed; account remains disabled; see migration-admin.stderr.log' }
    $Receipt=Get-Content -LiteralPath (Join-Path $Stage 'migration-receipt.json') -Raw | ConvertFrom-Json
    if ($Receipt.state -ne 'staged_acl_and_real_identity_verified') { throw 'Migration receipt is not verified' }
    $TaskName='Panse-Campaign-Restricted-Broker'
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) { throw 'Broker task already exists; do not overwrite it' }
    $Owner=[Security.Principal.WindowsIdentity]::GetCurrent().Name
    $Action=New-ScheduledTaskAction -Execute $Python -Argument '-I -u "C:\ProgramData\PanseCampaignExec\program\campaign_broker.py" --policy "C:\ProgramData\PanseCampaignExec\private-config\policy.json"' -WorkingDirectory "$Stage\program"
    $Trigger=New-ScheduledTaskTrigger -AtLogOn -User $Owner
    $Principal=New-ScheduledTaskPrincipal -UserId $Owner -LogonType Interactive -RunLevel Limited
    $Settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Description 'SID-scoped campaign pipe; no platform work without authorized request manifest' | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 2
    $Probe=Join-Path $PSScriptRoot 'probe_running_broker.py'
    $Verify=Start-Process -FilePath $Python -ArgumentList @('-u',('"'+$Probe+'"')) -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput (Join-Path $PSScriptRoot 'broker-probe.log') -RedirectStandardError (Join-Path $PSScriptRoot 'broker-probe.stderr.log')
    if ($Verify.ExitCode -ne 0) { throw 'Broker real identity probe failed; see broker-probe.stderr.log' }
    Copy-Item -LiteralPath (Join-Path $Stage 'broker-verification.json') -Destination (Join-Path $PSScriptRoot 'broker-verification.json')
    Copy-Item -LiteralPath (Join-Path $Stage 'migration-receipt.json') -Destination (Join-Path $PSScriptRoot 'migration-receipt.json')
    @{state='migration_verified_broker_task_started'; account_enabled=$false; original_chat_switched=$false; hard_permission_isolation=$false} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'migration-admin-result.json') -Encoding UTF8
} catch {
    Disable-LocalUser -Name PanseCampaignExec -ErrorAction Continue
    @{state='migration_incomplete'; reason=$_.Exception.Message; account_enabled=$false; hard_permission_isolation=$false} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'migration-admin-result.json') -Encoding UTF8
    exit 1
}
