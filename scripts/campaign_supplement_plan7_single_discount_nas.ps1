[CmdletBinding()]
param(
    [string]$SshHost = '15068803006@DS923plus',
    [int]$SshPort = 2222,
    [string]$SshKey = "$env:USERPROFILE\.ssh\panse_nas"
)

$ErrorActionPreference = 'Stop'
throw '该脚本已永久禁用：attempt 78838fdc2a7a5ac3a9c2380b 已完成，官方终态 4 成功 0 失败，逐 SKU 回读证据快照 14 已完成；严禁再次补报。'
$resolvedKey = (Resolve-Path -LiteralPath $SshKey -ErrorAction Stop).Path
$ssh = 'C:\Program Files\Git\usr\bin\ssh.exe'
if (-not (Test-Path -LiteralPath $ssh)) { throw "找不到 Git SSH: $ssh" }

$payload = [ordered]@{
    workflow_key = 'campaign:super-reduce:2026-09-01'
    plan_id = 7
    activity_ids = @('143780562424', '143936811502', '143939511827')
    target_activity_id = '143939511827'
    item_id = '1007407909979'
    rows = @(
        [ordered]@{ item_id='1007407909979'; sku_id='6240788711164'; expected_deduct='3508.50' }
        [ordered]@{ item_id='1007407909979'; sku_id='6228006543289'; expected_deduct='3315.99' }
        [ordered]@{ item_id='1007407909979'; sku_id='6228006543290'; expected_deduct='3101.93' }
        [ordered]@{ item_id='1007407909979'; sku_id='6228006543291'; expected_deduct='2911.38' }
    )
    scope_sha256 = 'eedccf8dd8ea9a5de1305c135f479703e7116a6fa547c0716edff800fbdec2f9'
    readonly_artifact_sha256 = 'e3eacb64d2e8ba3a15b1bc07dc0c20df970f3851240a40e6de3ebe7a06b2b85b'
    start_at = '2026-09-01 00:00:00'
    end_at = '2026-09-05 23:59:59'
}
$raw = $payload | ConvertTo-Json -Compress -Depth 5
$remote = 'sudo -n /var/packages/ContainerManager/target/usr/bin/docker exec -i panse-system-api-1 python -m app.cli.campaign_supplement_plan7_single_discount'
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
        throw "计划7单品立减固定补报失败，退出码 $exitCode$diagnostic"
    }
} finally {
    $OutputEncoding = $previousOutputEncoding
    if ($nativePreferenceExists) {
        $PSNativeCommandUseErrorActionPreference = $previousNativePreference
    }
}
