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

# All business identity fields and the reviewed row digest are immutable here.
# The remote CLI validates byte-for-byte semantic equality and performs one
# request only; it cannot select another plan or retry a failed/unknown attempt.
$payload = [ordered]@{
    workflow_key = 'campaign:super-reduce:2026-09-01'
    plan_id = 7
    expected_status = 'alarmed'
    expected_scope_sha256 = '73d73f5e78d5f7149b4425f6c7e9909e9892f037d4859498e6dea26f0163b7a4'
}
$raw = $payload | ConvertTo-Json -Compress
$remote = 'sudo -n /var/packages/ContainerManager/target/usr/bin/docker exec -i panse-system-api-1 python -m app.cli.campaign_resume_super_reduce_plan7'
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
                if ($detail.attempt_status) { $parts += "attempt_status=$($detail.attempt_status)" }
                if ($detail.plan_status) { $parts += "plan_status=$($detail.plan_status)" }
                if ($parts.Count -gt 0) { $diagnostic = '; ' + ($parts -join ', ') }
            } catch {
                $diagnostic = '; API 已返回响应 JSON（见上方原文）'
            }
        }
        throw "计划7一次性恢复 CLI 失败，退出码 $exitCode$diagnostic$responseJson"
    }
} finally {
    $OutputEncoding = $previousOutputEncoding
    if ($nativePreferenceExists) {
        $PSNativeCommandUseErrorActionPreference = $previousNativePreference
    }
}
