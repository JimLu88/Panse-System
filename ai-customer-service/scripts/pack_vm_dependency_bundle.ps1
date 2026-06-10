# Build a portable ZIP: requirements + wheels + one-click installer for offline VM.
#
#   powershell -ExecutionPolicy Bypass -File .\scripts\pack_vm_dependency_bundle.ps1
#   .\scripts\pack_vm_dependency_bundle.ps1 -Minimal
#   .\scripts\pack_vm_dependency_bundle.ps1 -WithPython
#   .\scripts\pack_vm_dependency_bundle.ps1 -Minimal -WithPython
#
# -WithPython: downloads python.org embeddable 3.11.x + get-pip.py into the zip (VM needs no system Python).
# Output: dist_vm_deps_bundle\AIWorkbench_deps_*.zip

param(
    [switch] $Minimal,
    [switch] $WithPython
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path $PSScriptRoot -Parent
Set-Location $RepoRoot

$reqName = if ($Minimal) { "requirements-vm-minimal.txt" } else { "requirements.txt" }
$ReqFile = Join-Path $RepoRoot $reqName
if (-not (Test-Path $ReqFile)) {
    Write-Host "Missing $reqName" -ForegroundColor Red
    exit 1
}

$EmbedVersion = "3.11.9"
$EmbedZipUrl = "https://www.python.org/ftp/python/$EmbedVersion/python-$EmbedVersion-embed-amd64.zip"
$GetPipUrl = "https://bootstrap.pypa.io/get-pip.py"

$py = $null
foreach ($c in @("py -3.11", "py -3", "py", "python")) {
    & cmd /c "$c --version" 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $py = $c; break }
}
if (-not $py) { $py = "python" }

$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$suffix = if ($Minimal) { "MIN" } else { "FULL" }
if ($WithPython) { $suffix = "${suffix}_PY" }
$stage = Join-Path $RepoRoot "vm_deps_staging_$stamp"
$wheels = Join-Path $stage "wheels"
$pyDir = Join-Path $stage "python"
New-Item -ItemType Directory -Path $wheels -Force | Out-Null

function Unembed-Pth {
    param([string]$Dir)
    $pth = Get-ChildItem -Path $Dir -Filter "python*._pth" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $pth) { throw "Embeddable package has no python*._pth under $Dir" }
    $lines = Get-Content -LiteralPath $pth.FullName
    $new = foreach ($line in $lines) {
        if ($line -match '^\s*#\s*import\s+site\s*$') { 'import site' }
        else { $line }
    }
    $txt = ($new -join "`n")
    if ($txt -notmatch '(?m)^import site\s*$') {
        $txt = $txt.TrimEnd() + "`nimport site`n"
    }
    Set-Content -LiteralPath $pth.FullName -Value $txt -NoNewline -Encoding ascii
}

try {
    Write-Host "Downloading pip/setuptools/wheel wheels (for offline bootstrap)..."
    & cmd /c "$py -m pip download pip setuptools wheel -d `"$wheels`""
    if ($LASTEXITCODE -ne 0) {
        Write-Host "pip download pip/setuptools/wheel failed." -ForegroundColor Red
        exit $LASTEXITCODE
    }

    Write-Host "Downloading project wheels with: $py (may take several minutes)..."
    $reqArg = "`"$ReqFile`""
    & cmd /c "$py -m pip download -r $reqArg -d `"$wheels`""
    if ($LASTEXITCODE -ne 0) {
        Write-Host "pip download failed." -ForegroundColor Red
        exit $LASTEXITCODE
    }

    Copy-Item $ReqFile (Join-Path $stage "requirements.txt")

    if ($WithPython) {
        Write-Host "Downloading embeddable Python $EmbedVersion ..."
        $zipTmp = Join-Path $env:TEMP "pyembed-$stamp.zip"
        Invoke-WebRequest -Uri $EmbedZipUrl -OutFile $zipTmp -UseBasicParsing
        New-Item -ItemType Directory -Path $pyDir -Force | Out-Null
        Expand-Archive -LiteralPath $zipTmp -DestinationPath $pyDir -Force
        Remove-Item $zipTmp -Force -ErrorAction SilentlyContinue
        Unembed-Pth -Dir $pyDir

        Write-Host "Downloading get-pip.py ..."
        Invoke-WebRequest -Uri $GetPipUrl -OutFile (Join-Path $stage "get-pip.py") -UseBasicParsing
    }

    $installPs1 = @'
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$req = Join-Path $here "requirements.txt"
$wh = Join-Path $here "wheels"
if (-not (Test-Path $req)) { Write-Host "requirements.txt missing"; exit 1 }

$embedExe = Join-Path $here "python\python.exe"
$getPip = Join-Path $here "get-pip.py"

if ((Test-Path $embedExe) -and (Test-Path $getPip)) {
    Write-Host "Using bundled Python: $embedExe"
    $pipInit = Join-Path $here "python\Lib\site-packages\pip\__init__.py"
    if (-not (Test-Path $pipInit)) {
        Write-Host "Bootstrapping pip (offline, from wheels)..."
        & cmd /c "`"$embedExe`" `"$getPip`" --no-warn-script-location --no-index --find-links=`"$wh`""
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    Write-Host "Upgrading pip/setuptools/wheel from wheels..."
    & cmd /c "`"$embedExe`" -m pip install --upgrade pip setuptools wheel --no-index --find-links=`"$wh`""
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Write-Host "Installing dependencies from wheels (offline)..."
    & cmd /c "`"$embedExe`" -m pip install --no-index --find-links=`"$wh`" -r `"$req`""
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Write-Host ""
    Write-Host "Done. Use this interpreter, e.g.:" -ForegroundColor Green
    Write-Host "  `"$embedExe`" -m pytest" -ForegroundColor Gray
    Write-Host "  `"$embedExe`" path\to\apps\ui\main.py" -ForegroundColor Gray
    exit 0
}

$pyCmd = $null
foreach ($c in @("py -3.11", "py -3", "py", "python")) {
    & cmd /c "$c --version" 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $pyCmd = $c; break }
}
if (-not $pyCmd) { $pyCmd = "python" }

Write-Host "Using system Python: $pyCmd"
& cmd /c "$pyCmd -m pip install --upgrade pip setuptools wheel"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (Test-Path $wh) {
    Write-Host "Installing from local wheels (offline)..."
    & cmd /c "$pyCmd -m pip install --no-index --find-links=`"$wh`" -r `"$req`""
} else {
    Write-Host "wheels folder missing; trying online install..."
    & cmd /c "$pyCmd -m pip install -r `"$req`""
}
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "All dependencies installed." -ForegroundColor Green
'@
    Set-Content -Path (Join-Path $stage "INSTALL_DEPS.ps1") -Value $installPs1 -Encoding UTF8

    $pyNote = if ($WithPython) {
        @(
            "This bundle includes embeddable Python under python\ (no system Python required).",
            "Run INSTALL_DEPS.bat once; then use python\python.exe for your app.",
            "Embeddable layout: scripts are not on PATH; prefer `"python\python.exe`" -m module."
        )
    } else {
        @("Install Python 3.11 x64 from python.org first, then run INSTALL_DEPS.bat.")
    }

    $readme = @(
        "AIWorkbench Python dependency bundle",
        "Built: $(Get-Date -Format 'yyyy-MM-dd HH:mm')",
        "Mode: $suffix",
        "",
        $pyNote,
        "",
        "Steps:",
        "  1. Unzip anywhere.",
        "  2. Double-click INSTALL_DEPS.bat",
        "",
        "Troubleshooting:",
        "  VC++ runtime may be required (PyQt6, onnxruntime).",
        "  If antivirus blocks pip, allow this folder or run from elevated PowerShell once.",
        "",
        "Minimal bundle: no sentence-transformers (lighter, weaker embedding features)."
    ) -join "`r`n"
    Set-Content -Path (Join-Path $stage "README_DEPS.txt") -Value $readme -Encoding UTF8

    $bat = @"
@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0INSTALL_DEPS.ps1"
if errorlevel 1 pause
pause
"@
    Set-Content -Path (Join-Path $stage "INSTALL_DEPS.bat") -Value $bat -Encoding ASCII

    $outDir = Join-Path $RepoRoot "dist_vm_deps_bundle"
    New-Item -ItemType Directory -Path $outDir -Force | Out-Null
    $zipName = "AIWorkbench_deps_${suffix}_$stamp.zip"
    $zipPath = Join-Path $outDir $zipName
    if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
    $items = Join-Path $stage "*"
    Compress-Archive -Path $items -DestinationPath $zipPath -Force

    Write-Host ""
    Write-Host ("OK: " + $zipPath) -ForegroundColor Green
    Write-Host ("Size approx: " + [math]::Round((Get-Item $zipPath).Length / 1MB, 1) + " MB")
}
finally {
    if (Test-Path $stage) {
        Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
    }
}
