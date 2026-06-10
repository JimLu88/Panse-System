"""应用装配。对应设计稿 architecture.md。"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .config import get_settings
from .database import init_db

app = FastAPI(title="AI Marketing System · 家具品牌内容矩阵", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    init_db()


app.include_router(router)


@app.get("/api/health")
def health() -> dict:
    s = get_settings()
    return {"ok": True, "llm_provider": s.llm_provider}


_WEB = Path(__file__).parent / "web"


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (_WEB / "index.html").read_text(encoding="utf-8")


app.mount("/static", StaticFiles(directory=_WEB), name="static")
