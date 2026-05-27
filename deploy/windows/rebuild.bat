@echo off
chcp 65001 >nul
REM 畔色 ERP — 强制重新构建镜像并重启
REM
REM 什么时候用:
REM   - 拉完新代码后版本号没变 (说明 Docker 用的还是旧镜像)
REM   - 修改了后端/前端代码想立刻生效
REM
REM 双击运行即可, 约 2-5 分钟完成

cd /d "%~dp0\..\.."

echo.
echo ========================================
echo   畔色 ERP — 重新构建镜像
echo ========================================
echo.

docker info >nul 2>&1
if errorlevel 1 (
    echo [X] Docker Desktop 没运行, 请先启动 Docker Desktop
    pause
    exit /b 1
)

echo [*] 开始构建 (约 2-5 分钟, 请耐心等待)...
echo.
docker compose build api web
if errorlevel 1 (
    echo.
    echo [X] 构建失败, 查看上方错误信息
    pause
    exit /b 1
)

echo.
echo [*] 构建完成, 重启容器...
docker compose up -d
if errorlevel 1 (
    echo [X] 启动失败
    pause
    exit /b 1
)

echo.
echo ========================================
echo   构建完成！刷新浏览器查看新版本
echo   前端: http://localhost:5173
echo ========================================
echo.
timeout /t 5 /nobreak >nul
