@echo off
chcp 65001 >nul
REM 把 panse_tray.py 打包成单 .exe (双击运行)
REM 跑一次即可, 输出 dist\PanseTray.exe

setlocal
cd /d "%~dp0"

echo [*] 检查 Python...
where python >nul 2>&1
if errorlevel 1 (
    echo [X] 没装 Python. 装 Python 3.10+ 后重跑.
    echo     下载: https://www.python.org/downloads/windows/
    pause
    exit /b 1
)

echo [*] 装依赖 (首次需 ~30 秒)...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet pystray pillow requests winotify pyinstaller pywin32
if errorlevel 1 (
    echo [X] 装依赖失败. 看上面错误
    pause
    exit /b 1
)

REM 让 pywin32 的 DLL 注册到正确位置 (解决序数找不到问题)
python -c "import pywin32_bootstrap" >nul 2>&1

echo [*] 打包 (大概 1 分钟)...
python -m PyInstaller ^
    --noconsole ^
    --onefile ^
    --name=PanseTray ^
    --collect-all winotify ^
    --collect-all pystray ^
    --hidden-import win32api ^
    --hidden-import win32con ^
    --hidden-import win32gui ^
    --hidden-import win32timezone ^
    --hidden-import pystray._win32 ^
    panse_tray.py

if errorlevel 1 (
    echo [X] 打包失败
    pause
    exit /b 1
)

echo.
echo ========================================
echo [✓] 打包完成!
echo.
echo 输出: %~dp0dist\PanseTray.exe
echo.
echo 双击 PanseTray.exe 启动托盘程序.
echo.
echo 设开机自启 (可选):
echo   1. Win+R 输 shell:startup 回车
echo   2. 把 PanseTray.exe 的快捷方式放进去
echo ========================================
echo.
pause
endlocal
