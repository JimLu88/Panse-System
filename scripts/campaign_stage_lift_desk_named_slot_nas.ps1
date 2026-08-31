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
$agentCandidates = @(
    (Join-Path $PSScriptRoot '..\..\Web-Agent程序\app\engine\uploader.py'),
    (Join-Path $PSScriptRoot '..\..\..\Web-Agent程序\app\engine\uploader.py')
)
$agentSource = $agentCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $agentSource) { throw '找不到畔色 ERP 项目内的主力 Web-Agent 源码' }
$sourceText = Get-Content -LiteralPath (Resolve-Path -LiteralPath $agentSource) -Raw
foreach ($marker in @(
    '130cm 带高台升降桌',
    'forbidden_old_unsaved_option_present_after_reload',
    'source_page_price_not_linked_to_erp_authority',
    'default_2000_eliminated',
    'allowed_differences'
)) {
    if (-not $sourceText.Contains($marker)) { throw "主力 Web-Agent 未满足新校准硬门，缺少: $marker" }
}
$remote = 'sudo -n /var/packages/ContainerManager/target/usr/bin/docker exec -i panse-system-api-1 python -m app.cli.campaign_stage_lift_desk_sku_slot'
& $ssh -i $resolvedKey -o BatchMode=yes -o ConnectTimeout=20 -p $SshPort $SshHost $remote
if ($LASTEXITCODE -ne 0) { throw "升降桌新命名只预览校准失败，退出码 $LASTEXITCODE" }
