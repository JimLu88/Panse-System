[CmdletBinding()]
param(
    [switch]$ExecuteOnce,
    [switch]$ReadbackOnly,
    [string]$SshHost = '15068803006@DS923plus',
    [int]$SshPort = 2222,
    [string]$SshKey = "$env:USERPROFILE\.ssh\panse_nas"
)

$ErrorActionPreference = 'Stop'
if ($ExecuteOnce -eq $ReadbackOnly) {
    throw 'Specify exactly one mode: -ExecuteOnce or -ReadbackOnly. No default execution is allowed.'
}
$resolvedKey = (Resolve-Path -LiteralPath $SshKey -ErrorAction Stop).Path
$ssh = 'C:\Program Files\Git\usr\bin\ssh.exe'
if (-not (Test-Path -LiteralPath $ssh)) { throw "Git SSH executable not found: $ssh" }
$payload = [ordered]@{
    workflow_key = 'campaign:super88:49462:49469'
    plan_id = 8
    expected_status = 'alarmed'
    recovery_version = 6
    mode = if ($ReadbackOnly) { 'readback' } else { 'execute' }
    confirmation = if ($ReadbackOnly) {
        'READBACK_ONLY_PLAN8_V6_NO_PLATFORM_WRITE'
    } else {
        'EXECUTE_ONCE_PLAN8_V6_6_ITEMS_78_SKUS_18_CUSTOM'
    }
    target_scope_sha256 = 'b239dc515b0f2442257e90fe30a1cda95e29f6ffd2ea123d6c53f6fd6a4feb1d'
}
$raw = $payload | ConvertTo-Json -Compress
$remote = 'sudo -n /var/packages/ContainerManager/target/usr/bin/docker exec -i panse-system-api-1 python -m app.cli.campaign_recover_plan8_final_v6'
$previousOutputEncoding = $OutputEncoding
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
try {
    $raw | & $ssh -i $resolvedKey -o BatchMode=yes -o ConnectTimeout=20 `
        -p $SshPort $SshHost $remote
    if ($LASTEXITCODE -ne 0) {
        throw "Plan 8 V6 command failed with exit code $LASTEXITCODE. If a write claim exists, use only -ReadbackOnly; never run -ExecuteOnce again."
    }
} finally {
    $OutputEncoding = $previousOutputEncoding
}
