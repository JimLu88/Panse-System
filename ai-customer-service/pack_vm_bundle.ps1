# Pack AIWorkbench for VM: exe + configs + data (话术 SQLite / image_kb) + images + instances.
#
# Recommended (话术 + 图库 + 数据库一次带走):
#   powershell -ExecutionPolicy Bypass -File .\pack_vm_bundle.ps1 -Full
#
# Smaller zip (skip large runtime logs under data\logs and repo \logs):
#   .\pack_vm_bundle.ps1 -Full -ExcludeHeavyLogs
#
# Minimal (exe + configs only):
#   .\pack_vm_bundle.ps1 -Minimal
#
# Requires: .\build_aiworkbench.ps1 -> dist\AIWorkbench.exe

param(
    [switch] $Full,
    [switch] $Minimal,
    [switch] $IncludeData,
    [switch] $IncludeImages,
    [switch] $IncludeInstances,
    [switch] $ExcludeHeavyLogs
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if ($Full -and $Minimal) {
    Write-Host "Use either -Full or -Minimal, not both." -ForegroundColor Red
    exit 1
}

if ($Full) {
    $IncludeData = $true
    $IncludeImages = $true
    $IncludeInstances = $true
}

if ($Minimal) {
    $IncludeData = $false
    $IncludeImages = $false
    $IncludeInstances = $false
}

$exe = Join-Path $PSScriptRoot "dist\AIWorkbench.exe"
if (-not (Test-Path $exe)) {
    Write-Host "Missing dist\AIWorkbench.exe - run build_aiworkbench.ps1 first." -ForegroundColor Red
    exit 1
}

function Invoke-RobocopyTree {
    param(
        [string] $SourceDir,
        [string] $DestDir,
        [string[]] $ExcludeDirNames = @()
    )
    if (-not (Test-Path $SourceDir)) { return }
    New-Item -ItemType Directory -Path $DestDir -Force | Out-Null
    $rcArgs = @($SourceDir, $DestDir, '/E', '/R:2', '/W:2', '/NFL', '/NDL', '/NJH', '/NJS', '/NP')
    foreach ($name in $ExcludeDirNames) {
        if ($name) {
            $rcArgs += '/XD'
            $rcArgs += $name
        }
    }
    & robocopy @rcArgs | Out-Null
    $code = $LASTEXITCODE
    # robocopy: 0–7 = OK (with various copy/no-copy meanings); >=8 = error
    if ($code -ge 8) {
        throw "robocopy failed exit=$code ($SourceDir -> $DestDir)"
    }
}

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$stage = Join-Path $PSScriptRoot "vm_bundle_staging_$stamp"
New-Item -ItemType Directory -Path $stage -Force | Out-Null

try {
    Copy-Item -Path $exe -Destination (Join-Path $stage "AIWorkbench.exe")

    $cfg = Join-Path $PSScriptRoot "configs"
    if (Test-Path $cfg) {
        Copy-Item -Path $cfg -Destination (Join-Path $stage "configs") -Recurse
    } else {
        Write-Host "Warning: configs folder not found; zip may be incomplete." -ForegroundColor Yellow
    }

    if ($IncludeData) {
        $dataSrc = Join-Path $PSScriptRoot "data"
        $dataDst = Join-Path $stage "data"
        if (Test-Path $dataSrc) {
            if ($ExcludeHeavyLogs) {
                Invoke-RobocopyTree -SourceDir $dataSrc -DestDir $dataDst -ExcludeDirNames @('logs')
            } else {
                Copy-Item -Path $dataSrc -Destination $dataDst -Recurse
            }
        }
    }

    if ($IncludeImages) {
        $img = Join-Path $PSScriptRoot "images"
        if (Test-Path $img) {
            Copy-Item -Path $img -Destination (Join-Path $stage "images") -Recurse
        }
    }

    if ($IncludeInstances) {
        $inst = Join-Path $PSScriptRoot "instances"
        if (Test-Path $inst) {
            Copy-Item -Path $inst -Destination (Join-Path $stage "instances") -Recurse
        }
    }

    $repoLogs = Join-Path $PSScriptRoot "logs"
    if ($IncludeData -and (-not $ExcludeHeavyLogs) -and (Test-Path $repoLogs)) {
        Copy-Item -Path $repoLogs -Destination (Join-Path $stage "logs") -Recurse
    }

    $readmeLines = @(
        "AIWorkbench VM bundle (full portable folder)",
        "Built: $(Get-Date -Format 'yyyy-MM-dd HH:mm')",
        "",
        "Includes:",
        "  AIWorkbench.exe",
        "  configs/     YAML, few_shot, shops, rules, shadow...",
        $(if ($IncludeData) { "  data/        sqlite app.db (话术/店铺等), image_kb/, optional logs" }),
        $(if ($IncludeImages) { "  images/      products + tutorials 图库" }),
        $(if ($IncludeInstances) { "  instances/   multi-profile AIWORKBENCH_PROFILE data" }),
        $(if ($IncludeData -and (-not $ExcludeHeavyLogs) -and (Test-Path $repoLogs)) { "  logs/        embedding jsonl etc. (dev tree)" }),
        "",
        "Usage on VM: unzip to one folder; keep exe, configs, data, images side by side; run exe.",
        "Workflow: copy whole folder back to host, open that path in Cursor, ask agent to edit files there.",
        "",
        "Security: configs/base_settings.yaml may contain API keys."
    ) | Where-Object { $_ -ne $null }
    $readme = $readmeLines -join "`r`n"

    $readmePath = Join-Path $stage "README_VM.txt"
    Set-Content -Path $readmePath -Value $readme -Encoding UTF8

    $outDir = Join-Path $PSScriptRoot "dist_vm_bundles"
    New-Item -ItemType Directory -Path $outDir -Force | Out-Null
    $suffix = if ($Full) { "_FULL" } elseif ($Minimal) { "_MIN" } else { "" }
    $zipName = "AIWorkbench_VM$suffix" + "_$stamp.zip"
    $zipPath = Join-Path $outDir $zipName

    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
    $items = Join-Path $stage "*"
    Compress-Archive -Path $items -DestinationPath $zipPath -Force

    Write-Host ""
    Write-Host ("OK: " + $zipPath) -ForegroundColor Green
    Write-Host ("Full=$Full Minimal=$Minimal IncludeData=$IncludeData IncludeImages=$IncludeImages IncludeInstances=$IncludeInstances ExcludeHeavyLogs=$ExcludeHeavyLogs")
}
finally {
    if (Test-Path $stage) {
        Remove-Item -Path $stage -Recurse -Force -ErrorAction SilentlyContinue
    }
}
