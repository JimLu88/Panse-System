# Verify the already migrated runtime only. Never rerun the ACL migration.
$ErrorActionPreference='Stop'
$Stage='C:\ProgramData\PanseCampaignExec'
if ((Get-LocalUser -Name PanseCampaignExec).Enabled) { throw 'Account already enabled; verification must not reset its password or disable it' }
$Python=Join-Path (Split-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) -Parent) 'Web-Agent程序\.venv\Scripts\python.exe'
try {
    $Task=Get-ScheduledTask -TaskName 'Panse-Campaign-Restricted-Broker'
    if ($Task.State -ne 'Running') { Start-ScheduledTask -TaskName $Task.TaskName }
    Start-Sleep -Seconds 1
    $Probe=Join-Path $PSScriptRoot 'probe_running_broker.py'
    $Process=Start-Process -FilePath $Python -ArgumentList @('-u',('"'+$Probe+'"')) -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput (Join-Path $PSScriptRoot 'broker-probe.log') -RedirectStandardError (Join-Path $PSScriptRoot 'broker-probe.stderr.log')
    if ($Process.ExitCode -ne 0) { throw 'Broker real identity check failed; see broker-probe.stderr.log' }
    Copy-Item -LiteralPath (Join-Path $Stage 'migration-receipt.json') -Destination (Join-Path $PSScriptRoot 'migration-receipt.json')
    Copy-Item -LiteralPath (Join-Path $Stage 'broker-verification.json') -Destination (Join-Path $PSScriptRoot 'broker-verification.json')
    @{state='migration_verified_broker_running';account_enabled=$false;original_chat_switched=$false;hard_permission_isolation=$false;acl_migration_repeated=$false} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'migration-admin-result.json') -Encoding UTF8
} catch {
    Disable-LocalUser -Name PanseCampaignExec -ErrorAction Continue
    @{state='broker_verification_incomplete';reason=$_.Exception.Message;account_enabled=$false;acl_migration_repeated=$false} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'broker-verification-error.json') -Encoding UTF8
    exit 1
}
