<#
.SYNOPSIS
    畔色 ERP 淘宝批量发货执行器启动脚本
.DESCRIPTION
    执行批次发货流程：读取批次 -> 核对文件 -> 批量导入 -> 拦截受理 -> 轮询终态 -> 逐单核验 -> 写回回执
.EXAMPLE
    .\run_batch_shipping.ps1 -BatchId 32e6d617-f609-4f92-90c3-45ca675fab7f -DryRun
    .\run_batch_shipping.ps1 -BatchId 32e6d617-f609-4f92-90c3-45ca675fab7f
#>
param (
    [Parameter(Mandatory = $true, HelpMessage = "ERP 发货批次 ID (UUID)")]
    [string]$BatchId,

    [switch]$DryRun,

    [string]$CdpUrl,

    [string]$ErpUrl = "http://192.168.31.21:8200",

    [int]$MaxPollSec = 300
)

Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host " 畔色 ERP 淘宝批量发货执行器 (Panse Fulfillment Runner)" -ForegroundColor Cyan
Write-Host "=======================================================" -ForegroundColor Cyan

if (-not $env:PANSE_API_TOKEN) {
    Write-Error "缺少环境变量 PANSE_API_TOKEN！请先设置操作员会话令牌: `$env:PANSE_API_TOKEN = 'your_token'"
    exit 1
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$runnerPy = Join-Path $scriptDir "fulfillment_batch_runner.py"

if (-not $CdpUrl) {
    if ($env:PANSE_SHIPPING_CDP_URL) {
        $CdpUrl = $env:PANSE_SHIPPING_CDP_URL
    } else {
        $portFile = Join-Path $scriptDir "..\..\..\Web-Agent程序\data\profiles\campaign-edge-dedicated-v1\DevToolsActivePort"
        if (Test-Path $portFile) {
            $port = (Get-Content $portFile -TotalCount 1).Trim()
            $CdpUrl = "http://127.0.0.1:$port"
            Write-Host "[INFO] 探测到专用浏览器 CDP 调试端点: $CdpUrl" -ForegroundColor Yellow
        }
    }
}

$argsList = @(
    $runnerPy,
    "--batch-id", $BatchId,
    "--erp-url", $ErpUrl,
    "--max-poll-sec", $MaxPollSec
)

if ($CdpUrl) {
    $argsList += @("--cdp-url", $CdpUrl)
}

if ($DryRun) {
    $argsList += "--dry-run"
}

Write-Host "[INFO] 启动发货执行器..." -ForegroundColor Green
python @argsList
exit $LASTEXITCODE
