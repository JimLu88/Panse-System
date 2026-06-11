"""应用装配。对应设计稿 architecture.md。

lifespan 启动轻量调度器；配 API_TOKEN 则 /api/* 走 Bearer 鉴权。
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .config import get_settings
from .database import init_db
from .services import scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(scheduler.loop())
    yield
    task.cancel()


app = FastAPI(title="AI Marketing System · 家具品牌内容矩阵", version="0.2.0",
              lifespan=lifespan)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """简单鉴权：配了 API_TOKEN 才启用（内网工具默认免鉴权可跑）。"""
    token = get_settings().api_token
    path = request.url.path
    if token and path.startswith("/api") and path != "/api/health":
        if request.headers.get("authorization") != f"Bearer {token}":
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return await call_next(request)


app.include_router(router)


@app.get("/api/health")
def health() -> dict:
    s = get_settings()
    return {"ok": True, "llm_provider": s.llm_provider, "auth": bool(s.api_token)}


_WEB = Path(__file__).parent / "web"


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (_WEB / "index.html").read_text(encoding="utf-8")


app.mount("/static", StaticFiles(directory=_WEB), name="static")
