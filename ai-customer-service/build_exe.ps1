$ErrorActionPreference = "Stop"

# 在仓库根目录执行：
#   powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
#
# 产物：
#   dist\AI客服工作台.exe

Set-Location -Path $PSScriptRoot

python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed with exit code $LASTEXITCODE" }

$logFile = Join-Path $PSScriptRoot ("pyinstaller-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".log")

Write-Host "Python:" (python --version)
Write-Host "PyInstaller check:"
python -m PyInstaller --version 2>&1 | Tee-Object -FilePath $logFile -Append
if ($LASTEXITCODE -ne 0) { throw "PyInstaller module not available (exit $LASTEXITCODE). See $logFile" }

Write-Host "Building... (log:" $logFile ")"
python -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --windowed `
  --name "AI客服工作台" `
  --distpath ".\\dist" `
  --workpath ".\\build" `
  --specpath "." `
  apps\\ui\\main.py 2>&1 | Tee-Object -FilePath $logFile -Append
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed with exit code $LASTEXITCODE. See $logFile" }

$exe = Join-Path $PSScriptRoot "dist\\AI客服工作台.exe"
Write-Host "Done."
Write-Host ("EXE should be at: {0}" -f $exe)
if (Test-Path $exe) {
  Write-Host "EXE exists."
} else {
  Write-Host "EXE NOT FOUND. Listing dist folder:"
  if (Test-Path (Join-Path $PSScriptRoot "dist")) {
    Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot "dist") -Force | Format-List
  } else {
    Write-Host "dist folder does not exist."
  }
}

