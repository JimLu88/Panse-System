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
    readonly_artifact_sha256 = '626a8abd62c6de30b6a6aff5294894c0641b70ac42afe8dee20e052bdd61038b'
    start_at = '2026-09-01 00:00:00'
    end_at = '2026-09-05 23:59:59'
    attempt_id = 'a7280fed1f9d638c41b8f8ae'
    attempt_snapshot_sha256 = '2d419fd6afb340707cc5406d3b835f45542177e4e8c4d27c8034be1948777dd0'
    confirmation = 'RESUME_ONCE_PLAN7_SAMPLE_CENT_ATTEMPT_A7280FED_AFTER_LIVE_MISSING_READBACK'
}
$raw = $payload | ConvertTo-Json -Compress -Depth 5
$remote = 'sudo -n /var/packages/ContainerManager/target/usr/bin/docker exec -i panse-system-api-1 python -m app.cli.campaign_resume_plan7_sample_cent'
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
        throw "计划7样块四SKU原 attempt 续接失败，退出码 $exitCode"
    }
} finally {
    $OutputEncoding = $previousOutputEncoding
    if ($nativePreferenceExists) {
        $PSNativeCommandUseErrorActionPreference = $previousNativePreference
    }
}
