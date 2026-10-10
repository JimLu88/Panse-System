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
if (-not $agentSource) {
    throw '找不到畔色 ERP 项目内的主力 Web-Agent 源码'
}
$sourceText = Get-Content -LiteralPath (
    Resolve-Path -LiteralPath $agentSource -ErrorAction Stop
) -Raw
foreach ($marker in @(
    'product_sku_recognition_dialog_guard',
    'name="在当前规格后添加", exact=True',
    'recognition_apply_clicked": True',
    'target_merchant_code_applied": False'
)) {
    if (-not $sourceText.Contains($marker)) {
        throw "主力 Web-Agent 尚未落地未保存规格添加修复，缺少标记: $marker"
    }
}

$entry = Join-Path $PSScriptRoot 'campaign_stage_lift_desk_sku_slot_nas.ps1'
& $entry -SshHost $SshHost -SshPort $SshPort -SshKey $SshKey

