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
$remote = 'sudo -n /var/packages/ContainerManager/target/usr/bin/docker exec -i panse-system-api-1 python -m app.cli.campaign_save_lift_desk_sku_slot_draft'
& $ssh -i $resolvedKey -o BatchMode=yes -o ConnectTimeout=20 -p $SshPort $SshHost $remote
if ($LASTEXITCODE -ne 0) { throw "升降桌轮换 SKU 平台草稿保存未完成，退出码 $LASTEXITCODE" }
