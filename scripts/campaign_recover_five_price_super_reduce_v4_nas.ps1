param(
    [string]$SshHost = '15068803006@DS923plus',
    [int]$SshPort = 2222,
    [string]$SshKey = "$env:USERPROFILE\.ssh\panse_nas"
)

$ErrorActionPreference = 'Stop'
$resolvedKey = (Resolve-Path -LiteralPath $SshKey -ErrorAction Stop).Path
$ssh = 'C:\Program Files\Git\usr\bin\ssh.exe'
if (-not (Test-Path -LiteralPath $ssh)) { throw "找不到 Git SSH: $ssh" }
$remote = "sudo -n /var/packages/ContainerManager/target/usr/bin/docker exec -i panse-system-api-1 python -m app.cli.campaign_recover_five_price_super_reduce_v4"
$previousNative = $PSNativeCommandUseErrorActionPreference
$PSNativeCommandUseErrorActionPreference = $false
try {
    $response = @(& $ssh -i $resolvedKey -o BatchMode=yes -o ConnectTimeout=20 `
        -p $SshPort $SshHost $remote 2>&1)
    $exitCode = $LASTEXITCODE
    $response | ForEach-Object { Write-Output ([string]$_) }
    if ($exitCode -ne 0) {
        throw "五项价格修正超级立减 V4 剩余项恢复失败，退出码 $exitCode。禁止自动重跑。"
    }
} finally {
    $PSNativeCommandUseErrorActionPreference = $previousNative
}
