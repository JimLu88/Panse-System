[CmdletBinding()]
param(
    [string]$SshHost = '15068803006@DS923plus',
    [int]$SshPort = 2222,
    [string]$SshKey = "$env:USERPROFILE\.ssh\panse_nas"
)

$ErrorActionPreference = 'Stop'
$resolvedKey = (Resolve-Path -LiteralPath $SshKey -ErrorAction Stop).Path
$ssh = 'C:\Program Files\Git\usr\bin\ssh.exe'
if (-not (Test-Path -LiteralPath $ssh)) {
    throw "找不到 Git SSH: $ssh"
}

$payload = [ordered]@{
    workflow_key = 'campaign:super-reduce:2026-09-01'
    plan_id = 7
    expected_status = 'alarmed'
    expected_item_scope_sha256 = 'd2ee3fd43a5c80d31799fc17b1b9f57c90db9f7ec7c78a0c88a501a7b51db2b8'
}
$raw = $payload | ConvertTo-Json -Compress
$remote = 'sudo -n /var/packages/ContainerManager/target/usr/bin/docker exec -i panse-system-api-1 python -m app.cli.campaign_execute_plan7_remaining'
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
        $responseJson = ''
        if ($jsonLine) {
            $responseJson = "; response_json=$jsonLine"
            try {
                $parsed = $jsonLine | ConvertFrom-Json -ErrorAction Stop
                $detail = if ($parsed.detail) { $parsed.detail } else { $parsed }
                $parts = @()
                if ($detail.error) { $parts += "error=$($detail.error)" }
                if ($detail.failed_batch) { $parts += "failed_batch=$($detail.failed_batch)" }
                if ($detail.plan_status) { $parts += "plan_status=$($detail.plan_status)" }
                if ($parts.Count -gt 0) { $diagnostic = '; ' + ($parts -join ', ') }
            } catch {
                $diagnostic = '; API 已返回响应 JSON（见上方原文）'
            }
        }
        throw "计划7剩余商品一次性报名失败，退出码 $exitCode$diagnostic$responseJson"
    }
} finally {
    $OutputEncoding = $previousOutputEncoding
    if ($nativePreferenceExists) {
        $PSNativeCommandUseErrorActionPreference = $previousNativePreference
    }
}
