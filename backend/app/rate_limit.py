"""Phase 6 P0: 限速 (slowapi).

主要保护:
    - /api/screenshots/*  截图 OCR — 单张 ~10K token, 烧钱
    - /api/importer/preview  AI 推断映射 — 也烧 token

通用配额: 5 次/分钟/用户; OCR 接口 10 次/分钟。
key 用 JWT 里的 username 提取, 没登录时退化到 IP.
"""
from __future__ import annotations

import os

from fastapi import Request
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address


def _client_ip(request: Request) -> str:
    """反代后取真实客户端 IP。

    app 只经 DSM 反代 + web nginx(已配 X-Forwarded-For)到达, 直连地址会是代理 IP,
    导致所有外部请求共用一个限速桶(误伤)。取 X-Forwarded-For 最左(原始客户端)修正。
    """
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    return get_remote_address(request)


def _key_func(request: Request) -> str:
    """优先用 JWT 里的 username 限速, 否则用真实客户端 IP."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        try:
            from app.services import auth_service
            payload = auth_service.decode_token(auth[7:])
            # JWT payload 里用户名字段是 "uname" (见 auth_service.create_token), 不是 "username"
            uname = payload.get("uname") if isinstance(payload, dict) else None
            if uname:
                return f"user:{uname}"
        except Exception:
            pass
    return f"ip:{_client_ip(request)}"


limiter = Limiter(
    key_func=_key_func,
    enabled=os.environ.get("DISABLE_RATE_LIMIT") != "1",
)


def install_rate_limit(app) -> None:
    """主入口: 在 main.py 调一次. 把 limiter 注册到 app + 错误处理."""
    app.state.limiter = limiter
    @app.exception_handler(RateLimitExceeded)
    async def _on_rate_exceeded(request: Request, exc: RateLimitExceeded):
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=429,
            content={"detail": f"请求过于频繁: {exc.detail}, 请稍后再试"},
        )
