@echo off
chcp 65001 >nul
REM 畔色 ERP 一键启动 (Windows)
REM 双击运行: 起所有容器 + 打开浏览器

setlocal
cd /d "%~dp0\..\.."

echo.
echo ========================================
echo   畔色孚格 ERP - 启动中
echo ========================================
echo.

REM 检查 Docker
docker info >nul 2>&1
if errorlevel 1 (
    echo [!] Docker Desktop 没运行, 尝试启动...
    start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
    echo [*] 等待 Docker 就绪 (最多 60 秒)...
    set /a count=0
    :wait_docker
    timeout /t 3 /nobreak >nul
    docker info >nul 2>&1
    if errorlevel 1 (
        set /a count+=3
        if %count% lss 60 goto wait_docker
        echo [X] Docker 启动失败, 请手动开 Docker Desktop 后再跑此脚本
        pause
        exit /b 1
    )
    echo [✓] Docker 就绪
)

echo [*] 启动容器...
docker compose up -d
if errorlevel 1 (
    echo [X] docker compose up 失败, 看上面错误
    pause
    exit /b 1
)

echo [*] 等 API 健康 (最多 60 秒)...
set /a count=0
:wait_api
timeout /t 3 /nobreak >nul
curl -s -o nul -w "" http://localhost:8000/api/health 2>nul
if errorlevel 1 (
    set /a count+=3
    if %count% lss 60 goto wait_api
    echo [!] API 还没响应, 但容器已启. 看 logs: docker compose logs api
) else (
    echo [✓] API 就绪
)

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
echo 关掉这个窗口不会停服务. 要停: docker compose down
echo.

REM 不暂停 — 启动完就退出, 浏览器已打开
endlocal
