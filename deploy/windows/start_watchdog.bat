@echo off
chcp 65001 >nul
REM 畔色 ERP 看门狗 — 从源码启动 (推荐)
REM
REM 为什么用这个而不是 PanseTray.exe:
REM   从源码跑时, 点「拉最新代码 + 重建」如果连看门狗自己都更新了,
REM   它会自动重启加载新代码 — 真正一键到底, 永远不用再手动重装。
REM   打包的 exe 做不到自更新 (git pull 改不了 exe 文件本身)。
REM
REM 双击即可。第一次会自动装依赖。

cd /d "%~dp0\..\.."

echo.
echo ========================================
echo   畔色 ERP 看门狗 (源码模式, 可自更新)
echo ========================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [X] 没找到 python, 请先装 Python 3 并勾选 "Add to PATH"
    pause
    exit /b 1
)

REM 确保依赖齐全 (已装会秒过)
echo [*] 检查依赖...
python -c "import pystray, PIL, requests" >nul 2>&1
if errorlevel 1 (
    echo [*] 安装依赖 pystray pillow requests winotify ...
    python -m pip install --quiet pystray pillow requests winotify
)

echo [+] 启动看门狗 (托盘图标). 关掉此窗口看门狗也会退出.
echo     要后台常驻请用 pythonw: pythonw deploy\windows\panse_tray.py
echo.
python deploy\windows\panse_tray.py
pause
