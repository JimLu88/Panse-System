[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ExportPath,
    [string]$SshHost = '15068803006@DS923plus',
    [int]$SshPort = 2222,
    [string]$SshKey = "$env:USERPROFILE\.ssh\panse_nas"
)

$ErrorActionPreference = 'Stop'
$resolvedExport = (Resolve-Path -LiteralPath $ExportPath -ErrorAction Stop).Path
$resolvedKey = (Resolve-Path -LiteralPath $SshKey -ErrorAction Stop).Path
$expectedName = '「26年淘宝9月超级88超级88现货」活动商品导出20260904182846.xlsx'
$expectedSize = 14994
$expectedSha = 'C7C22B57A95E7DB5F3CC8D8A0319EE4B1920A13E73204F1004BE3760D71D25DA'
$file = Get-Item -LiteralPath $resolvedExport
$actualSha = (Get-FileHash -LiteralPath $resolvedExport -Algorithm SHA256).Hash
if ($file.Name -cne $expectedName -or $file.Length -ne $expectedSize -or $actualSha -cne $expectedSha) {
    throw "The manual campaign export does not match the reviewed file. Expected $expectedName / $expectedSize bytes / $expectedSha."
}

$ssh = 'C:\Program Files\Git\usr\bin\ssh.exe'
if (-not (Test-Path -LiteralPath $ssh)) { throw "Git SSH executable not found: $ssh" }
$payload = [ordered]@{
    workflow_key = 'campaign:super88:49462:49469'
    plan_id = 8
    expected_status = 'alarmed'
    recovery_version = 8
    mode = 'resume_claimed_preupload_v27'
    confirmation = 'RESUME_ONCE_PLAN8_V8_AFTER_BOUND_DRAFT_EDITOR_HYDRATION_V27'
    target_scope_sha256 = '40bcd15a5567215d836a1735e0b7216aacc4677a068c36a0f1d68da3a9afdab4'
    manual_export_filename = $file.Name
    manual_export_size = [int]$file.Length
    manual_export_sha256 = $actualSha.ToLowerInvariant()
    manual_export_base64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($resolvedExport))
}
$raw = $payload | ConvertTo-Json -Compress
$remote = 'sudo -n /var/packages/ContainerManager/target/usr/bin/docker exec -i panse-system-api-1 python -m app.cli.campaign_recover_plan8_final_v8'
$previousOutputEncoding = $OutputEncoding
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
try {
    $raw | & $ssh -i $resolvedKey -o BatchMode=yes -o ConnectTimeout=20 `
        -p $SshPort $SshHost $remote
    if ($LASTEXITCODE -ne 0) {
        throw "Plan 8 V27 stopped with exit code $LASTEXITCODE. Do not run this command again; preserve and inspect the returned JSON."
    }
} finally {
    $OutputEncoding = $previousOutputEncoding
}
