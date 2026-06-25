@echo off
chcp 65001 >nul
REM 把"启动并守护"加入 Windows 开机自启(给当前用户)。再次运行可重复，无副作用。
set "TARGET=%~dp02-启动并守护.bat"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
powershell -NoProfile -Command ^
 "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%STARTUP%\内容矩阵工作台.lnk'); $s.TargetPath='%TARGET%'; $s.WorkingDirectory='%~dp0'; $s.Save()"
if errorlevel 1 ( echo [失败] 创建开机自启快捷方式出错。& pause & exit /b 1 )
echo ✅ 已设置开机自启：每次开机自动启动并守护工作台。
echo    如需取消：删除「%STARTUP%\内容矩阵工作台.lnk」即可。
pause
