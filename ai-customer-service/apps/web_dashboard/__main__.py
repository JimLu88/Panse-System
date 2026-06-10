"""
apps/web_dashboard/__main__.py
================================
入口：python -m apps.web_dashboard
由 MobileTab 的 subprocess.Popen 调用，在独立进程中运行。
"""
from __future__ import annotations

import uvicorn

from apps.web_dashboard.app import app

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8080,
        log_level="warning",
        access_log=False,
    )
