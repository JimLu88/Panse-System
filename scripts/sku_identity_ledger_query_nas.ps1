[CmdletBinding()]
param(
    [string]$ItemId,
    [string]$MerchantCode,
    [ValidateSet('json', 'csv')]
    [string]$Format = 'json',
    [string]$SshHost = '15068803006@DS923plus',
    [int]$SshPort = 2222,
    [string]$SshKey = "$env:USERPROFILE\.ssh\panse_nas"
)

$ErrorActionPreference = 'Stop'
if ($ItemId -and $ItemId -notmatch '^\d+$') { throw 'ItemId 必须是纯数字' }
if ($MerchantCode -and $MerchantCode -notmatch '^[A-Z][A-Z0-9_-]{9,63}$') { throw 'MerchantCode 格式不正确' }
$argsText = 'query'
if ($ItemId) { $argsText += " --item-id $ItemId" }
if ($MerchantCode) { $argsText += " --merchant-code $MerchantCode" }
$argsText += " --format $Format"
$resolvedKey = (Resolve-Path -LiteralPath $SshKey -ErrorAction Stop).Path
$ssh = 'C:\Program Files\Git\usr\bin\ssh.exe'
$remote = "sudo -n /var/packages/ContainerManager/target/usr/bin/docker exec -i panse-system-api-1 python -m app.cli.sku_identity_ledger $argsText"
& $ssh -i $resolvedKey -o BatchMode=yes -o ConnectTimeout=20 -p $SshPort $SshHost $remote
if ($LASTEXITCODE -ne 0) { throw "SKU 身份账本只读查询失败，退出码 $LASTEXITCODE" }
