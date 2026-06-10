$ErrorActionPreference = "Stop"

# 在本目录执行：
#   powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
#
# 产物：
#   dist\千牛聊天记录导出工具.exe

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
  --name "千牛聊天记录导出工具" `
  --distpath ".\\dist" `
  --workpath ".\\build" `
  --specpath "." `
  --collect-all "rapidocr_onnxruntime" `
  --collect-all "onnxruntime" `
  --collect-all "mss" `
  qn_gui.py 2>&1 | Tee-Object -FilePath $logFile -Append
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed with exit code $LASTEXITCODE. See $logFile" }

$exe = Join-Path $PSScriptRoot "dist\\千牛聊天记录导出工具.exe"
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

