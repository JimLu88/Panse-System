@echo off
chcp 65001 >nul
REM 畔色 ERP — 强制重新构建镜像并重启
REM 双击运行即可, 约 2-5 分钟完成

cd /d "%~dp0\..\.."

echo.
echo ========================================
echo   畔色 ERP — 重新构建镜像
echo ========================================
echo.

docker info >nul 2>&1
if errorlevel 1 (
    echo [X] Docker Desktop 没运行，请先启动 Docker Desktop
    pause
    exit /b 1
)

REM 读取当前 git 信息，注入到 Docker 构建参数（让版本号正确显示）
for /f %%i in ('git rev-parse HEAD 2^>nul') do set GIT_COMMIT=%%i
for /f %%i in ('git rev-parse --abbrev-ref HEAD 2^>nul') do set GIT_BRANCH=%%i
for /f "delims=" %%i in ('git log -1 "--format=%%s" 2^>nul') do set GIT_COMMIT_MSG=%%i
set BUILD_TIME=%date% %time%

if "%GIT_COMMIT%"=="" set GIT_COMMIT=unknown
if "%GIT_BRANCH%"=="" set GIT_BRANCH=unknown

echo [*] 当前 commit: %GIT_COMMIT:~0,7%
echo [*] 开始构建 (约 2-5 分钟，请耐心等待)...
echo.

docker compose build api web
if errorlevel 1 (
    echo.
    echo [X] 构建失败，查看上方错误信息
    pause
    exit /b 1
)

echo.
echo [*] 构建完成，重启容器...
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
echo   版本: %GIT_COMMIT:~0,7%
echo ========================================
echo.
timeout /t 5 /nobreak >nul
