# Run once as administrator after migration; enter password locally, never in chat.
param([switch]$CheckOnly)
$ErrorActionPreference='Stop'
$Stage='C:\ProgramData\PanseCampaignExec'
$Receipt=Get-Content -LiteralPath (Join-Path $Stage 'migration-receipt.json') -Raw | ConvertFrom-Json
if ($Receipt.state -ne 'staged_acl_and_real_identity_verified' -or -not $Receipt.ready_for_local_password_and_host_switch) { throw 'Migration and real identity checks have not passed' }
$Prepared=Get-Content -LiteralPath (Join-Path $Stage 'preparation-receipt.json') -Raw | ConvertFrom-Json
$User=Get-LocalUser -Name PanseCampaignExec
if ($User.SID.Value -ne $Prepared.sid) { throw 'Account identity changed; do not enable' }
$Manifest=Get-Content -LiteralPath (Join-Path $Stage 'installed-runtime.json') -Raw | ConvertFrom-Json
foreach ($File in $Manifest.files) {
    $Path=Join-Path (Join-Path $Stage 'program') $File.name
    if ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $File.sha256) { throw 'Restricted runtime hash changed' }
}
$Task=Get-ScheduledTask -TaskName 'Panse-Campaign-Restricted-Broker'
if ($Task.State -ne 'Running') { throw 'Maintenance broker must be running before activation' }
$Verified=Get-Content -LiteralPath (Join-Path $Stage 'broker-verification.json') -Raw | ConvertFrom-Json
if ($Verified.state -ne 'real_executor_sid_broker_verified' -or $Verified.executor_sid -ne $User.SID.Value -or -not $Verified.maintenance_command_rejected -or -not $Verified.fixed_capabilities_read) { throw 'Real identity broker verification has not passed' }
if ($CheckOnly) { @{state='ready_for_local_password_and_host_switch';account=$User.Name;enabled=$User.Enabled;original_chat_switched=$false} | ConvertTo-Json; return }
$Principal=[Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run as administrator' }
if ($User.Enabled) { Write-Host 'Account is already enabled. Password was not changed.'; return }
$Password=Read-Host 'Set a new password for PanseCampaignExec (local input only)' -AsSecureString
try {
    if ($Password.Length -lt 12) { throw 'Use at least 12 characters' }
    Set-LocalUser -Name PanseCampaignExec -Password $Password
    Enable-LocalUser -Name PanseCampaignExec
    Write-Host 'Account enabled. Switch Windows user to PanseCampaignExec. Current Codex chat is still under the old identity.'
    Write-Host 'Restricted client: C:\ProgramData\PanseCampaignExec\program\Invoke-Campaign.ps1'
} finally { $Password.Dispose() }
