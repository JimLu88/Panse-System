[CmdletBinding()]
param(
    [string]$SshHost = '15068803006@DS923plus',
    [int]$SshPort = 2222,
    [string]$SshKey = "$env:USERPROFILE\.ssh\panse_nas",
    [switch]$Preflight
)

$ErrorActionPreference = 'Stop'
$resolvedKey = (Resolve-Path -LiteralPath $SshKey -ErrorAction Stop).Path
$ssh = 'C:\Program Files\Git\usr\bin\ssh.exe'
if (-not (Test-Path -LiteralPath $ssh)) { throw "找不到 Git SSH: $ssh" }
$payload = [ordered]@{
    workflow_key = 'campaign:super88:49462:49469'
    plan_id = 8
    operation = 'plan8_sku_mapping_repair'
    scope_sha256 = '53fdc3f82a338e8e526491782c6e06791eeebd7ef50bead385a8541b31ef765c'
    official_product_export_sha256 = 'fb9e552254f29f8e022f799edd5a6a01b7dfc6653112dba3ee5286bb4270b984'
    authorization_ref = 'user_approved_eight_sku_mapping:2026-09-02'
}
$raw = $payload | ConvertTo-Json -Compress
$mode = if ($Preflight) { ' --preflight' } else { '' }
$remote = 'sudo -n /var/packages/ContainerManager/target/usr/bin/docker exec -i panse-system-api-1 python -m app.cli.campaign_repair_plan8_sku_mappings' + $mode
$previousOutputEncoding = $OutputEncoding
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
try {
    $raw | & $ssh -i $resolvedKey -o BatchMode=yes -o ConnectTimeout=20 `
        -p $SshPort $SshHost $remote
    if ($LASTEXITCODE -ne 0) {
        throw "计划8八SKU数据库修复失败，退出码 $LASTEXITCODE；不得重复运行"
    }
} finally {
    $OutputEncoding = $previousOutputEncoding
}
