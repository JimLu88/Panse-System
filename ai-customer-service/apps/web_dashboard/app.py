"""
apps/web_dashboard/app.py
==========================
FastAPI 应用入口。
  - 挂载 /api/* 路由
  - 挂载 /static 静态文件目录
  - GET / 返回 index.html

启动方式：python -m apps.web_dashboard
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from apps.web_dashboard.api import control, stats

_STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title="手机接待 · 局域网仪表盘",
    description="实时查看接待数据，并远程控制所有设备",
    version="1.4.0",
    docs_url=None,
    redoc_url=None,
)

app.include_router(stats.router)
app.include_router(control.router)

app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(_STATIC_DIR / "index.html"))


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
