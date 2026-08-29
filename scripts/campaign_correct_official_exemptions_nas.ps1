[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$')]
    [string]$WorkflowKey,
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, [int]::MaxValue)]
    [int]$PlanId,
    [Parameter(Mandatory = $true)]
    [AllowEmptyCollection()]
    [ValidateScript({ $_ -match '^\d{4,}$' })]
    [string[]]$ExpectedOfficialExemptItemIds,
    [Parameter(Mandatory = $true)]
    [AllowEmptyCollection()]
    [ValidateScript({ $_ -match '^\d{4,}$' })]
    [string[]]$OfficialExemptItemIds,
    [string]$SshHost = '15068803006@DS923plus',
    [int]$SshPort = 2222,
    [string]$SshKey = "$env:USERPROFILE\.ssh\panse_nas"
)

$ErrorActionPreference = 'Stop'
$resolvedKey = (Resolve-Path -LiteralPath $SshKey -ErrorAction Stop).Path
$ssh = 'C:\Program Files\Git\usr\bin\ssh.exe'
if (-not (Test-Path -LiteralPath $ssh)) {
    throw "找不到 Git SSH: $ssh"
}

$payload = [ordered]@{
    workflow_key = $WorkflowKey
    plan_id = $PlanId
    expected_official_exempt_item_ids = @($ExpectedOfficialExemptItemIds)
    official_exempt_item_ids = @($OfficialExemptItemIds)
}
$raw = $payload | ConvertTo-Json -Compress
$remote = 'sudo -n /var/packages/ContainerManager/target/usr/bin/docker exec -i panse-system-api-1 python -m app.cli.campaign_correct_official_exemptions'
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
        $jsonLine = $responseLines | ForEach-Object { [string]$_ } |
            Where-Object { $_ -match '^\s*\{' } | Select-Object -Last 1
        $responseJson = if ($jsonLine) { "; response_json=$jsonLine" } else { '' }
        throw "计划级官方豁免修正 CLI 失败，退出码 $exitCode$responseJson"
    }
} finally {
    $OutputEncoding = $previousOutputEncoding
    if ($nativePreferenceExists) {
        $PSNativeCommandUseErrorActionPreference = $previousNativePreference
    }
}
