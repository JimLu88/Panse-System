[CmdletBinding()]
param(
    [string]$SshHost = '15068803006@DS923plus',
    [int]$SshPort = 2222,
    [string]$SshKey = "$env:USERPROFILE\.ssh\panse_nas"
)

$ErrorActionPreference = 'Stop'
$resolvedKey = (Resolve-Path -LiteralPath $SshKey -ErrorAction Stop).Path
$ssh = 'C:\Program Files\Git\usr\bin\ssh.exe'
if (-not (Test-Path -LiteralPath $ssh)) { throw "Git SSH executable not found: $ssh" }
$payload = [ordered]@{
    workflow_key = 'campaign:super88:49462:49469'
    plan_id = 8
    expected_status = 'alarmed'
    recovery_version = 8
    mode = 'resume_preclaim_v2'
    confirmation = 'RESUME_ONCE_PLAN8_V8_AFTER_EDITOR_LOCATOR_FIX_V2'
    target_scope_sha256 = '40bcd15a5567215d836a1735e0b7216aacc4677a068c36a0f1d68da3a9afdab4'
}
$raw = $payload | ConvertTo-Json -Compress
$remote = 'sudo -n /var/packages/ContainerManager/target/usr/bin/docker exec -i panse-system-api-1 python -m app.cli.campaign_recover_plan8_final_v8'
$previousOutputEncoding = $OutputEncoding
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
try {
    $raw | & $ssh -i $resolvedKey -o BatchMode=yes -o ConnectTimeout=20 `
        -p $SshPort $SshHost $remote
    if ($LASTEXITCODE -ne 0) {
        throw "Plan 8 V8 editor-fix continuation stopped with exit code $LASTEXITCODE. Do not run this command again; inspect the returned JSON."
    }
} finally {
    $OutputEncoding = $previousOutputEncoding
}
