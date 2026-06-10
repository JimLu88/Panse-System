# One-click install all Python deps from repo (needs internet on VM).
# Run from repo root:  powershell -ExecutionPolicy Bypass -File .\scripts\vm_install_deps_online.ps1

$ErrorActionPreference = "Stop"
# This file lives in <repo>\scripts\
$RepoRoot = Split-Path $PSScriptRoot -Parent
$Req = Join-Path $RepoRoot "requirements.txt"
if (-not (Test-Path $Req)) {
    Write-Host "requirements.txt not found. RepoRoot=$RepoRoot" -ForegroundColor Red
    exit 1
}

$py = $null
foreach ($c in @("py -3.11", "py -3", "py", "python")) {
    try {
        & cmd /c "$c --version" 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $py = $c; break }
    } catch { }
}
if (-not $py) { $py = "python" }

Write-Host "Using: $py"
Write-Host "Upgrading pip..."
& cmd /c "$py -m pip install --upgrade pip setuptools wheel"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Installing from $Req ..."
& cmd /c "$py -m pip install -r `"$Req`""
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "Done." -ForegroundColor Green
