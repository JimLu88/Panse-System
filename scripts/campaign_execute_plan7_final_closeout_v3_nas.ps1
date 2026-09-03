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
    expected_status = 'alarmed'
    bundle_id = 'f6dd4cb3b7ffea16178efd6b'
    expected_source_sha256 = '7c1476c840526e459d5ec5755ca1072a676e5c40e77c71d779b15cb30c0ef58e'
    expected_policy_sha256 = 'dbb4a7294636fb2f5bfd115efd561976eb6684cbfc00b9ed2f0f4aa1850dfe33'
    expected_manifest_sha256 = '40337eb5781ce17a55c2787535e7137c6bde39fbdfb78a15676f6530322de013'
    expected_item_scope_sha256 = 'e6a3f59b93f5329a928263c976190cf707ef3c9db39c7654df6d1678f1d0c24e'
    recovery_id = 'plan7-final-closeout-product-export-claim-v3'
    expected_web_agent_commit = 'c7fdea3ed4594983d8f8baea896ff8e65088f2b8'
}
$raw = $payload | ConvertTo-Json -Compress
$remote = 'sudo -n /var/packages/ContainerManager/target/usr/bin/docker exec -i panse-system-api-1 python -m app.cli.campaign_execute_plan7_final_closeout_v3'
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
        throw "计划7最终收口 V3 CLI 失败，退出码 $exitCode$responseJson"
    }
} finally {
    $OutputEncoding = $previousOutputEncoding
    if ($nativePreferenceExists) {
        $PSNativeCommandUseErrorActionPreference = $previousNativePreference
    }
}
