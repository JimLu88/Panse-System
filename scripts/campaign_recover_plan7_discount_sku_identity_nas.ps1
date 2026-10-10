[CmdletBinding()]
param(
    [string]$EvidencePath = 'D:\AI\畔色ERP系统\活动准备\plan7-live-sku-identity-1047741902625.xlsx',
    [string]$SshHost = '15068803006@DS923plus',
    [int]$SshPort = 2222,
    [string]$SshKey = "$env:USERPROFILE\.ssh\panse_nas"
)

$ErrorActionPreference = 'Stop'
$resolvedKey = (Resolve-Path -LiteralPath $SshKey -ErrorAction Stop).Path
$resolvedEvidence = (Resolve-Path -LiteralPath $EvidencePath -ErrorAction Stop).Path
$ssh = 'C:\Program Files\Git\usr\bin\ssh.exe'
if (-not (Test-Path -LiteralPath $ssh)) { throw "找不到 Git SSH: $ssh" }
$expectedSha = 'CDF6502BBF4C048824A0AD5F1545D6335FAA117A854F3C624773C1E610A9A72B'
$actualSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $resolvedEvidence).Hash
if ($actualSha -ne $expectedSha) {
    throw "官方商品导出 SHA256 不匹配；禁止执行 SKU 身份恢复"
}
$payload = [ordered]@{
    workflow_key = 'campaign:super-reduce:2026-09-01'
    plan_id = 7
    expected_old_attempt_id = 'a701400096c131d9ae2c3e38'
    official_product_export_sha256 = $expectedSha.ToLowerInvariant()
    official_product_export_b64 = [Convert]::ToBase64String(
        [IO.File]::ReadAllBytes($resolvedEvidence))
    expected_new_scope_sha256 = '80e603ca57aa2974ab892f9ad1738e3dbd3b00d3b026ad80dd7aed642085371a'
}
$raw = $payload | ConvertTo-Json -Compress
$remote = 'sudo -n /var/packages/ContainerManager/target/usr/bin/docker exec -i panse-system-api-1 python -m app.cli.campaign_recover_plan7_discount_sku_identity'
$previousOutputEncoding = $OutputEncoding
$nativePreferenceExists = Test-Path Variable:PSNativeCommandUseErrorActionPreference
if ($nativePreferenceExists) {
    $previousNativePreference = $PSNativeCommandUseErrorActionPreference
    $PSNativeCommandUseErrorActionPreference = $false
}
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
try {
    $responseLines = @($raw | & $ssh -i $resolvedKey -o BatchMode=yes `
        -o ConnectTimeout=20 -p $SshPort $SshHost $remote 2>&1)
    $exitCode = $LASTEXITCODE
    $responseLines | ForEach-Object { Write-Output ([string]$_) }
    if ($exitCode -ne 0) {
        $jsonLine = $responseLines | ForEach-Object { [string]$_ } |
            Where-Object { $_ -match '^\s*\{' } | Select-Object -Last 1
        $diagnostic = ''
        if ($jsonLine) {
            try {
                $parsed = $jsonLine | ConvertFrom-Json -ErrorAction Stop
                $detail = if ($parsed.detail) { $parsed.detail } else { $parsed }
                if ($detail.error) { $diagnostic = "; error=$($detail.error)" }
            } catch { $diagnostic = '; API 已返回响应 JSON（见上方原文）' }
        }
        throw "计划7 SKU身份修正后4行恢复失败，退出码 $exitCode$diagnostic"
    }
} finally {
    $OutputEncoding = $previousOutputEncoding
    if ($nativePreferenceExists) {
        $PSNativeCommandUseErrorActionPreference = $previousNativePreference
    }
}
