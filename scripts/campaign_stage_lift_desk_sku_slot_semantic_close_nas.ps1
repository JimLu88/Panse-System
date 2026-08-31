[CmdletBinding()]
param(
    [string]$SshHost = '15068803006@DS923plus',
    [int]$SshPort = 2222,
    [string]$SshKey = "$env:USERPROFILE\.ssh\panse_nas"
)

$ErrorActionPreference = 'Stop'
$agentCandidates = @(
    (Join-Path $PSScriptRoot '..\..\Web-Agent程序\app\engine\uploader.py'),
    (Join-Path $PSScriptRoot '..\..\..\Web-Agent程序\app\engine\uploader.py')
)
$agentSource = $agentCandidates |
    Where-Object { Test-Path -LiteralPath $_ } |
    Select-Object -First 1
if (-not $agentSource) { throw '找不到畔色 ERP 项目内的主力 Web-Agent 源码' }
$sourceText = Get-Content -LiteralPath (
    Resolve-Path -LiteralPath $agentSource -ErrorAction Stop
) -Raw
foreach ($marker in @(
    'chrome_close_controls',
    'in_top_chrome',
    'unclassified_buttons',
    'target_merchant_code_applied": False'
)) {
    if (-not $sourceText.Contains($marker)) {
        throw "主力 Web-Agent 尚未落地关闭控件分类修复，缺少标记: $marker"
    }
}

$entry = Join-Path $PSScriptRoot 'campaign_stage_lift_desk_sku_slot_nas.ps1'
& $entry -SshHost $SshHost -SshPort $SshPort -SshKey $SshKey

