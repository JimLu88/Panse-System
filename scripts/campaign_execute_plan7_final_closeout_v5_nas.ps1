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
    expected_plan_status = 'resume_executing'
    failed_v4_invocation_id = 'a531725a704ce1a910ddc008'
    prepared_attempt_id = 'c7df358081734428cbf05cea'
    prepared_bundle_id = 'd7c563f9a793233e1ceab7b4'
    expected_bundle_source_sha256 = 'c148d01cd73c9afd62f8008e40b16f4f5755c88b57b4a47e363f59209c2138b4'
    expected_bundle_manifest_sha256 = '9d287a19027c45754c0cf8860046ca8184be7e290ac5f76ed4153928aae76d33'
    failed_v4_request_id = '3cf6aa79e038'
    failed_v4_receipt_sha256 = 'ca8259c1b7dc237553ecbc87b630708739d670a9fd050e1082b4ce9120987963'
    recovery_id = 'plan7-final-closeout-v4-bundle-consumption-v5'
}
$raw = $payload | ConvertTo-Json -Compress
$remote = 'sudo -n /var/packages/ContainerManager/target/usr/bin/docker exec -i panse-system-api-1 python -m app.cli.campaign_execute_plan7_final_closeout_v5'
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
        $responseJson = if ($jsonLine) { "; response_json=$jsonLine" } else { '' }
        throw "计划7最终收口 V5 CLI 失败，退出码 $exitCode$responseJson"
    }
} finally {
    $OutputEncoding = $previousOutputEncoding
    if ($nativePreferenceExists) {
        $PSNativeCommandUseErrorActionPreference = $previousNativePreference
    }
}
