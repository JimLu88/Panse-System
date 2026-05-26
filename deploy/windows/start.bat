@echo off
chcp 65001 >nul
REM 畔色 ERP 一键启动 (Windows)
REM 双击运行: 起所有容器 + 打开浏览器

cd /d "%~dp0\..\.."

echo.
echo ========================================
echo   畔色孚格 ERP - 启动中
echo ========================================
echo.

docker info >nul 2>&1
if errorlevel 1 goto need_docker
goto docker_ready

:need_docker
echo [!] Docker Desktop 没运行, 尝试启动...
start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
echo [*] 等待 Docker 就绪 (最多 60 秒)...
set wait_d=0
:wait_docker_loop
timeout /t 3 /nobreak >nul
docker info >nul 2>&1
if not errorlevel 1 goto docker_ready
set /a wait_d=wait_d+3
if %wait_d% geq 60 goto docker_fail
goto wait_docker_loop

:docker_fail
echo [X] Docker 启动失败, 请手动开 Docker Desktop 后再跑此脚本
pause
exit /b 1

:docker_ready
echo [+] Docker 就绪
echo [*] 启动容器...
docker compose up -d
if errorlevel 1 goto compose_fail

echo [*] 等 API 健康 (最多 60 秒)...
set wait_a=0
:wait_api_loop
timeout /t 3 /nobreak >nul
curl -s -o nul http://localhost:8000/api/health 2>nul
if not errorlevel 1 goto api_ready
set /a wait_a=wait_a+3
if %wait_a% geq 60 goto api_timeout
goto wait_api_loop

:api_timeout
echo [!] API 60 秒还没响应, 但容器已启. 检查: docker compose logs api
goto open_browser

:api_ready
echo [+] API 就绪

:open_browser
echo.
echo [*] 打开浏览器: http://localhost:5173
start "" "http://localhost:5173"
echo.
echo ========================================
echo   ERP 启动完成
echo   前端:  http://localhost:5173
echo   API:   http://localhost:8000/docs
echo ========================================
echo.
echo 关掉这个窗口不会停服务. 要停: 双击 stop.bat
echo.
timeout /t 5 /nobreak >nul
exit /b 0

:compose_fail
echo [X] docker compose up 失败, 看上面错误
pause
exit /b 1
