@echo off
chcp 65001 >nul
REM 与本目录 AIWorkbench.exe 放在同一文件夹；可复制三份，改成不同 profile 名称。
cd /d "%~dp0"
start "" "%~dp0AIWorkbench.exe" --profile 店铺甲
