param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('single_discount', 'super_reduce')]
    [string]$Phase
)

$ErrorActionPreference = 'Stop'
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptRoot
& (Join-Path $repoRoot 'scripts\invoke_campaign_container_cli.ps1') `
    -Module 'app.cli.campaign_correct_five_price_issues' `
    -Arguments @($Phase)
