$ErrorActionPreference = 'Stop'
try {
    & (Join-Path $PSScriptRoot 'Prepare-RestrictedAccount.ps1') -Apply
    Copy-Item -LiteralPath 'C:\ProgramData\PanseCampaignExec\preparation-receipt.json' -Destination (Join-Path $PSScriptRoot 'preparation-receipt.json') -ErrorAction Stop
    @{state='prepared_disabled'; hard_permission_isolation=$false} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'admin-result.json') -Encoding UTF8
} catch {
    @{state='preparation_failed'; error_type=$_.Exception.GetType().Name; reason=$_.Exception.Message; hard_permission_isolation=$false} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'admin-result.json') -Encoding UTF8
    exit 1
}
