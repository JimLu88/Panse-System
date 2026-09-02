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
    throw '必须且只能显式选择 -ExecuteOnce 或 -ReadbackOnly；无参数不会执行'
}
$resolvedKey = (Resolve-Path -LiteralPath $SshKey -ErrorAction Stop).Path
$ssh = 'C:\Program Files\Git\usr\bin\ssh.exe'
if (-not (Test-Path -LiteralPath $ssh)) { throw "找不到 Git SSH: $ssh" }
$payload = [ordered]@{
    workflow_key = 'campaign:super88:49462:49469'
    plan_id = 8
    expected_status = 'alarmed'
    recovery_version = 3
    mode = if ($ReadbackOnly) { 'readback' } else { 'execute' }
    confirmation = if ($ReadbackOnly) {
        'READBACK_ONLY_PLAN8_V3_NO_PLATFORM_WRITE'
    } else {
        'EXECUTE_ONCE_PLAN8_V3_6_ITEMS_78_SKUS_18_CUSTOM'
    }
    target_scope_sha256 = 'b239dc515b0f2442257e90fe30a1cda95e29f6ffd2ea123d6c53f6fd6a4feb1d'
}
$raw = $payload | ConvertTo-Json -Compress
$remote = 'sudo -n /var/packages/ContainerManager/target/usr/bin/docker exec -i panse-system-api-1 python -m app.cli.campaign_recover_plan8_final_v3'
$previousOutputEncoding = $OutputEncoding
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
try {
    $raw | & $ssh -i $resolvedKey -o BatchMode=yes -o ConnectTimeout=20 `
        -p $SshPort $SshHost $remote
    if ($LASTEXITCODE -ne 0) {
        throw "计划8最终恢复V3失败，退出码 $LASTEXITCODE；若写入锁已生成，只能加 -ReadbackOnly 只读核验，严禁再次执行"
    }
} finally {
    $OutputEncoding = $previousOutputEncoding
}
