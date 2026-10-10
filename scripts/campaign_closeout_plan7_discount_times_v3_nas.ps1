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
    attempt_id = '17f82907f8f3707736bbe2b2'
    request_id = 'plan7-time-recovery-v2-e21baed97be3e080'
    web_agent_job_id = 'job2'
    external_request_id = 'f5d44ce64b90'
    confirmed_activity_ids = @(
        '143780562424', '143936811502', '143939511827')
}
$raw = $payload | ConvertTo-Json -Compress -Depth 4
$remote = 'sudo -n /var/packages/ContainerManager/target/usr/bin/docker exec -i panse-system-api-1 python -m app.cli.campaign_closeout_plan7_discount_times_v3'
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
        throw "计划7三活动V3只读收口失败，退出码 $exitCode$diagnostic"
    }
} finally {
    $OutputEncoding = $previousOutputEncoding
    if ($nativePreferenceExists) {
        $PSNativeCommandUseErrorActionPreference = $previousNativePreference
    }
}
