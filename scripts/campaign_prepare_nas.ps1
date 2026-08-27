[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$InputFile,
    [string]$SshHost = '15068803006@DS923plus',
    [int]$SshPort = 2222,
    [string]$SshKey = "$env:USERPROFILE\.ssh\panse_nas"
)

$ErrorActionPreference = 'Stop'
$resolvedInput = (Resolve-Path -LiteralPath $InputFile -ErrorAction Stop).Path
$resolvedKey = (Resolve-Path -LiteralPath $SshKey -ErrorAction Stop).Path
$ssh = 'C:\Program Files\Git\usr\bin\ssh.exe'
if (-not (Test-Path -LiteralPath $ssh)) {
    throw "找不到 Git SSH: $ssh"
}

$raw = Get-Content -LiteralPath $resolvedInput -Raw -Encoding UTF8
try {
    $parsed = $raw | ConvertFrom-Json -ErrorAction Stop
} catch {
    throw "活动准备文件不是有效 JSON: $resolvedInput"
}
if ($null -eq $parsed.workflow_key -or [string]::IsNullOrWhiteSpace([string]$parsed.workflow_key)) {
    throw '活动准备文件缺少 workflow_key'
}

$remote = 'sudo -n /var/packages/ContainerManager/target/usr/bin/docker exec -i panse-system-api-1 python -m app.cli.campaign_prepare'
$previousOutputEncoding = $OutputEncoding
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
try {
    $raw | & $ssh -i $resolvedKey -o BatchMode=yes -o ConnectTimeout=20 -p $SshPort $SshHost $remote
    if ($LASTEXITCODE -ne 0) {
        throw "活动准备 CLI 失败，退出码 $LASTEXITCODE"
    }
} finally {
    $OutputEncoding = $previousOutputEncoding
}
