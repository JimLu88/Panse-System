@echo off
chcp 65001 >nul
REM 畔色 ERP 停止 (Windows)
REM 双击: 停所有容器, 不删数据

setlocal
cd /d "%~dp0\..\.."

echo [*] 停止容器 (数据保留)...
docker compose down
echo.
echo [✓] 已停止. 数据保留在 docker volume.
echo.
echo 重新启动: 双击 start.bat
echo 删除所有数据 (慎用!): docker compose down -v
echo.
pause
endlocal
