$ErrorActionPreference = "Stop"
# 项目根目录（v1.5.x 起搬迁到 D:\AI\AI 客服系统）
$root = "D:\AI\AI 客服系统"
Set-Location -LiteralPath $root

# 自动 patch +1（v1.5.0 → v1.5.1 → v1.5.2 ...）；同步写 apps/release_info.py + apps/__version__.py
Write-Host "正在递增版本号..."
$newVer = (& py "$root\_bump_ver.py").Trim()
if (-not $newVer) {
    Write-Host "✗ _bump_ver.py 输出为空，打包终止"
    exit 1
}
Write-Host "版本号已升至：v$newVer"

Write-Host ""
Write-Host "Cleaning build cache to ensure source changes take effect..."
if (Test-Path (Join-Path $root "build")) {
    Remove-Item -LiteralPath (Join-Path $root "build") -Recurse -Force
}
Write-Host ""
Write-Host "Running PyInstaller..."
py -m PyInstaller AIWorkbench.spec --noconfirm

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$srcExe = Join-Path $root "dist\AIWorkbench.exe"
$dstExe = Join-Path $root "dist\AIWorkbench-v$newVer.exe"

if (Test-Path $srcExe) {
    # 容错：如果同名版本号 EXE 被占用（用户开着旧版），不致命，跳过拷贝
    try {
        Copy-Item -Path $srcExe -Destination $dstExe -Force -ErrorAction Stop
        $sizeMb = [math]::Round((Get-Item $srcExe).Length / 1MB, 1)
        Write-Host ""
        Write-Host "OK: $dstExe  (${sizeMb} MB)"
    } catch {
        Write-Host ""
        Write-Host "⚠ Copy-Item 失败（可能旧版 EXE 正被占用）：$($_.Exception.Message)"
        Write-Host "  AIWorkbench.exe 已是最新；请关掉旧的 v$newVer.exe 后手动拷贝。"
    }
} else {
    Write-Host "dist\AIWorkbench.exe not found."
    exit 1
}