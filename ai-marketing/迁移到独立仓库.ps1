# 把 ai-marketing 切成独立仓库 + 拉到本地工作副本
# 用法：在你 PC 的 PowerShell 里运行   ./迁移到独立仓库.ps1
# 作用：从 JimLu88/Panse-System 切出 ai-marketing 子目录(连历史) → 强制推到
#       JimLu88/ai-marketing-system 的 main → 克隆到 D:\AI\AI Marketing Syeyem
# 前提：本机已装 Git、已登录 GitHub(对 ai-marketing-system 有推送权限)

$ErrorActionPreference = "Stop"
$base   = "D:\AI"
$target = "AI Marketing Syeyem"            # 目标工作副本文件夹名
$erpUrl = "https://github.com/JimLu88/Panse-System.git"
$soloUrl= "https://github.com/JimLu88/ai-marketing-system.git"

Set-Location $base

# 1) 临时克隆 ERP 仓库
if (Test-Path "_panse_tmp") { Remove-Item -Recurse -Force "_panse_tmp" }
git clone $erpUrl _panse_tmp
Set-Location "_panse_tmp"

# 2) 把 ai-marketing 连历史切成独立分支
git subtree split --prefix=ai-marketing -b ai-marketing-only

# 3) 强制推到独立空仓库的 main
git push $soloUrl ai-marketing-only:main --force

# 4) 删临时克隆
Set-Location $base
Remove-Item -Recurse -Force "_panse_tmp"

# 5) 原文件夹改名备份（里面是最早的设计稿），克隆独立仓库到目标路径
if (Test-Path $target) {
    $bak = "$target`_old_backup"
    if (Test-Path $bak) { Remove-Item -Recurse -Force $bak }
    Rename-Item $target $bak
    Write-Host "原文件夹已备份为：$bak"
}
git clone $soloUrl $target

Write-Host ""
Write-Host "✅ 完成。独立仓库已就绪：$base\$target"
Write-Host "启动方式："
Write-Host "  cd `"$base\$target`""
Write-Host "  pip install -r requirements.txt"
Write-Host "  python -m app.seed"
Write-Host "  uvicorn app.main:app --reload"
