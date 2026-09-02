[CmdletBinding()]
param(
    [string]$SshHost = '15068803006@DS923plus',
    [int]$SshPort = 2222,
    [string]$SshKey = "$env:USERPROFILE\.ssh\panse_nas"
)

$ErrorActionPreference = 'Stop'
$resolvedKey = (Resolve-Path -LiteralPath $SshKey -ErrorAction Stop).Path
$ssh = 'C:\Program Files\Git\usr\bin\ssh.exe'
if (-not (Test-Path -LiteralPath $ssh)) { throw "找不到 Git SSH: $ssh" }
$payload = [ordered]@{
    workflow_key = 'campaign:super88:49462:49469'
    plan_id = 8
    expected_status = 'alarmed'
    recovery_version = 2
}
$raw = $payload | ConvertTo-Json -Compress
$remote = 'sudo -n /var/packages/ContainerManager/target/usr/bin/docker exec -i panse-system-api-1 python -m app.cli.campaign_recover_plan8_final_v2'
$previousOutputEncoding = $OutputEncoding
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
try {
    $raw | & $ssh -i $resolvedKey -o BatchMode=yes -o ConnectTimeout=20 `
        -p $SshPort $SshHost $remote
    if ($LASTEXITCODE -ne 0) {
        throw "计划8最终恢复V2执行失败，退出码 $LASTEXITCODE；先读官方终态，不得盲目重跑"
    }
} finally {
    $OutputEncoding = $previousOutputEncoding
}
