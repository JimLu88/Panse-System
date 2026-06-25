@echo off
chcp 65001 >nul
cd /d "%~dp0..\.."
echo ============================================
echo   内容矩阵工作台 - 安装依赖(只需运行一次)
echo ============================================
python --version >nul 2>&1
if errorlevel 1 (
  echo [错误] 没检测到 Python。请先去 python.org 安装 Python 3.10+，
  echo        安装时务必勾选 "Add Python to PATH"，装完重开本窗口再运行。
  pause
  exit /b 1
)
echo 正在安装依赖（首次较慢，请耐心等待）...
python -m pip install -r requirements.txt
if errorlevel 1 ( echo [错误] 依赖安装失败，请把上面红字截图发我。& pause & exit /b 1 )
if not exist marketing.db (
  echo 正在初始化演示数据(账号/选题/草稿/知乎初稿/数字人)...
  python -m app.seed
)
echo.
echo ✅ 安装完成！以后双击「2-启动并守护.bat」即可使用。
pause
