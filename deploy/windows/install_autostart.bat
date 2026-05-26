@echo off
chcp 65001 >nul
REM 把 PanseTray.exe 注册到 Windows 开机启动
REM 需要先跑 build_exe.bat 生成 dist\PanseTray.exe

setlocal
cd /d "%~dp0"

set EXE_PATH=%~dp0dist\PanseTray.exe

if not exist "%EXE_PATH%" (
    echo [X] 没找到 %EXE_PATH%
    echo     先跑 build_exe.bat 生成 exe
    pause
    exit /b 1
)

set STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
set LNK="%STARTUP_DIR%\PanseTray.lnk"

echo [*] 创建快捷方式到开机启动文件夹...
powershell -NoProfile -Command "$s=(New-Object -COM WScript.Shell).CreateShortcut(%LNK%); $s.TargetPath='%EXE_PATH%'; $s.WorkingDirectory='%~dp0'; $s.Description='畔色 ERP 托盘看门狗'; $s.Save()"

if exist %LNK% (
    echo [✓] 已注册. 下次开机自动启动托盘.
    echo.
    echo 快捷方式位置: %STARTUP_DIR%\PanseTray.lnk
    echo 想取消开机自启 → 删掉这个 .lnk 文件
) else (
    echo [X] 创建快捷方式失败
)
echo.
pause
endlocal
