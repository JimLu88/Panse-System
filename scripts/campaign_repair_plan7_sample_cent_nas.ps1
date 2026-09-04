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
    workflow_key = 'campaign:super-reduce:2026-09-01'
    plan_id = 7
    activity_ids = @('143780562424', '143936811502', '143939511827')
    target_activity_id = '143939511827'
    item_id = '719436834260'
    rows = @(
        [ordered]@{ item_id='719436834260'; sku_id='6285733543660'; expected_deduct='5.99' }
        [ordered]@{ item_id='719436834260'; sku_id='5024477897617'; expected_deduct='5.99' }
        [ordered]@{ item_id='719436834260'; sku_id='6120623944056'; expected_deduct='5.99' }
        [ordered]@{ item_id='719436834260'; sku_id='6282622238127'; expected_deduct='5.99' }
    )
    scope_sha256 = 'e2c8bfa1e3db32d0937971ea8481414baacc2d8f82e63484810168efc2f97fce'
    readonly_artifact_sha256 = '80a9d3d406e4936fe1c801c53fb2119edc752cdb52de32914f8dc3cc1e1cfc8a'
    start_at = '2026-09-01 00:00:00'
    end_at = '2026-09-05 23:59:59'
}
$raw = $payload | ConvertTo-Json -Compress -Depth 5
$remote = 'sudo -n /var/packages/ContainerManager/target/usr/bin/docker exec -i panse-system-api-1 python -m app.cli.campaign_repair_plan7_sample_cent'
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
        throw "计划7样块四SKU单品立减修复失败，退出码 $exitCode$diagnostic"
    }
} finally {
    $OutputEncoding = $previousOutputEncoding
    if ($nativePreferenceExists) {
        $PSNativeCommandUseErrorActionPreference = $previousNativePreference
    }
}
