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
    workflow_key = 'campaign:super88:49462:49469'
    plan_id = 8
    expected_status = 'alarmed'
    expected_original_attempt_id = '14ddfc8e428148b66f61c7aa'
    expected_original_scope_sha256 = '7c1f20ed3693bbef62b0cf53d1f3a16acf969e8c615952b61b4db24a6c83665f'
    expected_full_signup_scope_sha256 = 'a08cf3892aecfac211b04bbce7761eac969b75814a72eeb1dece89a64e4dc5c5'
    expected_pending_scope_sha256 = 'f60f4eda9a238702dc2b69cf0db61fd3ca0ded844cf6ccd3032650f56a663805'
    expected_policy_sha256 = '66487550dd76974d415dd00e3b3153d6605a4b24bae6d314792e457501480076'
    expected_candidate_sha256 = 'bddba1f579359389d85928c0ccff75b7e9595ac767504121de16b3c661560070'
}
$raw = $payload | ConvertTo-Json -Compress
$remote = 'sudo -n /var/packages/ContainerManager/target/usr/bin/docker exec -i panse-system-api-1 python -m app.cli.campaign_recover_plan8_signup'
$previousOutputEncoding = $OutputEncoding
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
try {
    $raw | & $ssh -i $resolvedKey -o BatchMode=yes -o ConnectTimeout=20 `
        -p $SshPort $SshHost $remote
    if ($LASTEXITCODE -ne 0) {
        throw "计划8只补报名执行失败，退出码 $LASTEXITCODE；成功、失败或未知结果均禁止自动重试"
    }
} finally {
    $OutputEncoding = $previousOutputEncoding
}
