"""幂等中间件 (优化 #3): 带 Idempotency-Key 头的 POST/PUT/PATCH, 同 key 在 TTL 内
重复到达 → 直接 409 拒绝, 防双击/弱网重试重复创建 (工厂单/订单/补单等)。

实现取"重复即拒"而非"回放响应", 避免改写响应体的坑; 首次请求若失败(非 2xx)会释放
key 以允许真正重试。内存存储, 本部署 uvicorn 单进程足够; 多 worker 场景需换 Redis。
客户端用法: 每次用户主动提交生成一个新 UUID 作为 Idempotency-Key, 重试时复用同一个。
"""
from __future__ import annotations

import threading
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

_TTL = 600          # key 保留 10 分钟
_MAX = 5000         # 防内存无限增长
_lock = threading.Lock()
_seen_at: dict[str, float] = {}


def _prune(now: float) -> None:
    if len(_seen_at) <= _MAX:
        return
    for k in [k for k, t in list(_seen_at.items()) if now - t > _TTL]:
        _seen_at.pop(k, None)


class IdempotencyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        key = request.headers.get("idempotency-key")
        if not key or request.method not in ("POST", "PUT", "PATCH"):
            return await call_next(request)
        now = time.monotonic()
        with _lock:
            ts = _seen_at.get(key)
            if ts is not None and now - ts <= _TTL:
                return JSONResponse(
                    {"detail": "重复提交已忽略 (相同 Idempotency-Key)", "idempotent": True},
                    status_code=409,
                )
            _seen_at[key] = now
            _prune(now)
        try:
            response = await call_next(request)
        except Exception:
            with _lock:
                _seen_at.pop(key, None)   # 异常 → 释放 key, 允许重试
            raise
        if not (200 <= response.status_code < 300):
            with _lock:
                _seen_at.pop(key, None)   # 失败 → 释放 key, 允许重试
        return response
