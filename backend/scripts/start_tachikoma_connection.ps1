$ErrorActionPreference = "Stop"

$backendRoot = Split-Path -Parent $PSScriptRoot
$python = "C:\Users\lzdwy\AppData\Local\Programs\Python\Python311\python.exe"
$logRoot = Join-Path $backendRoot "..\logs"
$stdoutLog = Join-Path $logRoot "tachikoma-connection.out.log"
$stderrLog = Join-Path $logRoot "tachikoma-connection.err.log"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Panse System Python runtime is missing: $python"
}

$null = New-Item -ItemType Directory -Path $logRoot -Force
$env:HOST = "127.0.0.1"
$env:PORT = "8000"
$env:PANSE_TACHIKOMA_CONNECTION_ONLY = "1"
$env:PANSE_ERP_READ_BASE_URL = "http://192.168.31.21:8200"
$env:DISABLE_WATCHDOG = "1"
$env:DISABLE_SCHEDULER = "1"
$env:ENABLE_FEISHU_BOT = "0"
$env:PANSE_DISABLE_NOTIFY = "1"

Set-Location -LiteralPath $backendRoot
$ErrorActionPreference = "Continue"
& $python -m app.tachikoma_run 1>> $stdoutLog 2>> $stderrLog
exit $LASTEXITCODE
