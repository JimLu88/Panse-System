@echo off
setlocal enabledelayedexpansion

echo =======================================================
echo  畔色 ERP 淘宝批量发货执行器 (Panse Fulfillment Runner)
echo =======================================================

if "%~1"=="" (
    echo 用法:
    echo   run_batch_shipping.bat --batch-id ^<UUID^> [--dry-run] [--cdp-url ^<URL^>] [--erp-url ^<URL^>]
    echo.
    echo 环境变量:
    echo   PANSE_API_TOKEN         操作员访问令牌 (必须提供，严禁自动生成)
    echo   PANSE_SHIPPING_CDP_URL  专用 Edge CDP 调试地址 (可选，未指定时自动探测)
    exit /b 1
)

if "%PANSE_API_TOKEN%"=="" (
    echo [ERROR] 缺少环境变量 PANSE_API_TOKEN！
    echo 请先设置当前操作员会话令牌:
    echo   set PANSE_API_TOKEN=your_token_here
    exit /b 1
)

set SCRIPT_DIR=%~dp0
set RUNNER_PY=%SCRIPT_DIR%fulfillment_batch_runner.py

rem 若未显式配置 PANSE_SHIPPING_CDP_URL，则尝试从 Web-Agent 专用 Edge 配置读取端口
if "%PANSE_SHIPPING_CDP_URL%"=="" (
    set DEVTOOLS_FILE=%SCRIPT_DIR%..\..\..\Web-Agent程序\data\profiles\campaign-edge-dedicated-v1\DevToolsActivePort
    if exist "!DEVTOOLS_FILE!" (
        set /p DEVTOOLS_PORT=<"!DEVTOOLS_FILE!"
        set PANSE_SHIPPING_CDP_URL=http://127.0.0.1:!DEVTOOLS_PORT!
        echo [INFO] 探测到专用浏览器 CDP 调试端点: !PANSE_SHIPPING_CDP_URL!
    )
)

python "%RUNNER_PY%" %*
exit /b %ERRORLEVEL%
