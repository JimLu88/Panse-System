@echo off
chcp 65001 >nul
cd /d "%~dp0..\.."
title 内容矩阵工作台 · 看门狗(崩溃自动重启 / 关窗即停止)
if not exist marketing.db ( python -m app.seed )
echo 正在打开工作台 http://127.0.0.1:8000 ...
start "" http://127.0.0.1:8000
:loop
echo.
echo [%date% %time%] 启动服务中... (Ctrl+C 或关闭本窗口可停止)
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
echo [%date% %time%] 服务已退出，5 秒后自动重启...
timeout /t 5 /nobreak >nul
goto loop
