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
    expected_start_at = '2026-09-01 00:00:00'
    expected_end_at = '2026-09-01 23:59:59'
    target_start_at = '2026-09-01 00:00:00'
    target_end_at = '2026-09-05 23:59:59'
    failed_attempt_id = '9cf79b441a5fdbd56de061a7'
    prewrite_receipts = @(
        [ordered]@{
            request_id = 'feb0a38dc0ec'
            web_agent_job_id = 'job1'
            attempt_id = $null
            platform_write = $false
            submitted = $false
            confirmed_activity_ids = @()
        },
        [ordered]@{
            request_id = '32110b92632a'
            web_agent_job_id = 'job2'
            attempt_id = '9cf79b441a5fdbd56de061a7'
            platform_write = $false
            submitted = $false
            confirmed_activity_ids = @()
        }
    )
}
$raw = $payload | ConvertTo-Json -Compress -Depth 6
$remote = 'sudo -n /var/packages/ContainerManager/target/usr/bin/docker exec -i panse-system-api-1 python -m app.cli.campaign_recover_plan7_discount_times'
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
        throw "计划7三活动一次性恢复失败，退出码 $exitCode$diagnostic"
    }
} finally {
    $OutputEncoding = $previousOutputEncoding
    if ($nativePreferenceExists) {
        $PSNativeCommandUseErrorActionPreference = $previousNativePreference
    }
}
