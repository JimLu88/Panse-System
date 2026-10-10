[CmdletBinding()]
param(
    [string]$SshHost = '15068803006@DS923plus',
    [int]$SshPort = 2222,
    [string]$SshKey = "$env:USERPROFILE\.ssh\panse_nas",
    [string]$EvidenceDir = 'D:\AI\畔色ERP系统\活动准备'
)

$ErrorActionPreference = 'Stop'
$resolvedKey = (Resolve-Path -LiteralPath $SshKey -ErrorAction Stop).Path
$ssh = 'C:\Program Files\Git\usr\bin\ssh.exe'
if (-not (Test-Path -LiteralPath $ssh)) { throw "找不到 Git SSH: $ssh" }

$payload = [ordered]@{
    workflow_key = 'campaign:super-reduce:2026-09-01'
    plan_id = 7
    expected_attempt_id = '782299846f10d86ef4742c20'
    expected_manifest_sha256 = '2fa747d77823ed63baee82c5dbcc0d0fff6e248f77583dd4c9b074fa57d5c30d'
}
$raw = $payload | ConvertTo-Json -Compress
$remote = 'sudo -n /var/packages/ContainerManager/target/usr/bin/docker exec -i panse-system-api-1 python -m app.cli.campaign_audit_plan7_partial_signup'
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
    $jsonLine = $responseLines | ForEach-Object { [string]$_ } |
        Where-Object { $_ -match '^\s*\{' } | Select-Object -Last 1
    if (-not $jsonLine) {
        $responseLines | ForEach-Object { Write-Output ([string]$_) }
        throw "计划7部分导入只读核对未返回JSON，退出码 $exitCode"
    }
    $result = $jsonLine | ConvertFrom-Json -ErrorAction Stop
    if ($exitCode -ne 0 -or -not $result.ok) {
        Write-Output $jsonLine
        throw "计划7部分导入只读核对失败，退出码 $exitCode"
    }
    if (-not (Test-Path -LiteralPath $EvidenceDir)) {
        New-Item -ItemType Directory -Path $EvidenceDir | Out-Null
    }
    $feedbackPath = Join-Path $EvidenceDir 'plan7-partial-import-official-feedback-782299846f10d86ef4742c20.xlsx'
    $receiptPath = Join-Path $EvidenceDir 'plan7-partial-import-audit-782299846f10d86ef4742c20.json'
    [IO.File]::WriteAllBytes(
        $feedbackPath, [Convert]::FromBase64String([string]$result.feedback_xlsx_b64))
    $result.PSObject.Properties.Remove('feedback_xlsx_b64')
    $result | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $receiptPath -Encoding utf8
    [pscustomobject]@{
        ok = $true
        feedback_path = $feedbackPath
        receipt_path = $receiptPath
        feedback_sha256 = (Get-FileHash -LiteralPath $feedbackPath -Algorithm SHA256).Hash.ToLowerInvariant()
        draft_imported_item_ids = $result.draft_imported_item_ids
        failed_item_ids = $result.failed_item_ids
        official_active_item_ids = $result.official_active_item_ids
        official_paused_or_pending_item_ids = $result.official_paused_or_pending_item_ids
        official_not_exact_item_ids = $result.official_not_exact_item_ids
        safe_failed_only_recovery_available = $result.safe_failed_only_recovery_available
    } | ConvertTo-Json -Depth 8
} finally {
    $OutputEncoding = $previousOutputEncoding
    if ($nativePreferenceExists) {
        $PSNativeCommandUseErrorActionPreference = $previousNativePreference
    }
}
