# Build single-file AIWorkbench.exe (PyQt6 UI + bundled configs template).
# Run from repo root:  powershell -ExecutionPolicy Bypass -File .\build_aiworkbench.ps1
#
# 每次运行自动递增 patch 版本号，打包产物命名为 AIWorkbench-vX.Y.Z.exe（同时保留 AIWorkbench.exe）。

$ErrorActionPreference = "Stop"

# 项目根目录（与 apps\ 同级）
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($root)) {
    $root = $PSScriptRoot
}
if ([string]::IsNullOrWhiteSpace($root)) {
    $root = (Get-Location).Path
}
Set-Location -LiteralPath $root

# 1. 读取并递增版本号
$verFile = Join-Path $root "apps\release_info.py"
$verContent = Get-Content $verFile -Raw -Encoding UTF8

if ($verContent -match '__version__\s*=\s*"(\d+)\.(\d+)\.(\d+)"') {
    $major = [int]$Matches[1]
    $minor = [int]$Matches[2]
    $patch = [int]$Matches[3] + 1
} else {
    Write-Error "无法从 apps\release_info.py 解析版本号，请确认格式为 __version__ = ""X.Y.Z"""
    exit 1
}

$newVer = "$major.$minor.$patch"
$today  = (Get-Date -Format "yyyy-MM-dd")

$verContent = $verContent -replace '__version__\s*=\s*"[\d.]+"',  "__version__ = `"$newVer`""
$verContent = $verContent -replace 'BUILD_DATE\s*=\s*"[\d-]+"', "BUILD_DATE = `"$today`""

$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($verFile, ($verContent.TrimEnd() + [Environment]::NewLine), $utf8NoBom)

# UI 标题栏读 apps\__version__.py，须与 release_info 同步
$uiVerFile = Join-Path $root "apps\__version__.py"
$uiVerContent = @"
"""应用版本号；由 build_aiworkbench.ps1 与 release_info.py 同步，请勿手改 patch。"""
__version__ = "$newVer"
BUILD_DATE = "$today"
"@
[System.IO.File]::WriteAllText($uiVerFile, ($uiVerContent.TrimEnd() + [Environment]::NewLine), $utf8NoBom)

Write-Host "版本号已更新：v$newVer  ($today)  [release_info + __version__]"

# 2. PyInstaller 打包
Write-Host ""
Write-Host "Running PyInstaller..."
py -m PyInstaller AIWorkbench.spec --noconfirm

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

# 3. 将产物额外复制一份带版本号的文件，方便归档
$srcExe = Join-Path $root "dist\AIWorkbench.exe"
$dstExe = Join-Path $root "dist\AIWorkbench-v$newVer.exe"

if (Test-Path $srcExe) {
    Copy-Item -Path $srcExe -Destination $dstExe -Force
    $sizeMb = [math]::Round((Get-Item $srcExe).Length / 1MB, 1)
    Write-Host ""
    Write-Host "OK: $dstExe  (${sizeMb} MB)"
    Write-Host "    （dist\AIWorkbench.exe 为同一文件的无版本号副本）"
    if ($sizeMb -gt 300) {
        Write-Warning "exe 体积异常偏大 (>${sizeMb}MB)，请检查是否误打包 dist 旧产物或 torch"
    }
    Write-Host "部署时请同步 configs\query_rewrite.yaml 到 exe 同目录 configs\"
} else {
    Write-Host "dist\AIWorkbench.exe not found."
    exit 1
}
