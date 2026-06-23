"""Server-Sent Events 推送总线 (Phase 12, 替代 NotificationBell 30s 轮询).

进程内的简单 in-memory 发布订阅. 每个登录的浏览器连接 → 一个 asyncio.Queue.
事件发布时 fan-out 到所有 queue.

生产场景多进程时, 需换 Redis pub/sub. 单进程 docker 部署够用。
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator, Optional

_logger = logging.getLogger("panse.sse")

# 全局订阅者列表 (asyncio.Queue)
_SUBSCRIBERS: set[asyncio.Queue] = set()


def publish(event: str, payload: dict) -> None:
    """同步发布. 内部把消息放进每个 subscriber queue (非阻塞).

    业务调用方 (alert_service / order_event_service) 可调.
    失败/没人订阅都不抛.
    """
    if not _SUBSCRIBERS:
        return
    msg = {"event": event, "data": payload}
    for q in list(_SUBSCRIBERS):
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:  # pragma: no cover
            _logger.warning("SSE 订阅队列已满, 丢弃事件")


async def subscribe(heartbeat_s: float | None = None) -> AsyncIterator[dict]:
    """新 client 连接时调. yield 每个事件; 连接断开自动清理.

    heartbeat_s: 空闲这么多秒没事件时, yield 一条 {"event": "_heartbeat"} 让上层发保活注释,
    防代理 (nginx proxy_read_timeout) 把静默的 SSE 连接当超时断开 → 前端反复重连 (磁盘琐碎写入,
    让 NAS 盘睡不着, 2026-06-23 降负载)。None = 不发心跳 (旧行为)。

    用法 (FastAPI StreamingResponse):
        async def event_gen():
            async for msg in sse_bus.subscribe(heartbeat_s=25):
                yield f"event: {msg['event']}\\ndata: {json.dumps(msg['data'])}\\n\\n"
    """
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    _SUBSCRIBERS.add(q)
    try:
        while True:
            if heartbeat_s is None:
                msg = await q.get()
            else:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=heartbeat_s)
                except asyncio.TimeoutError:
                    yield {"event": "_heartbeat"}   # 超时不丢订阅 (q 仍在 _SUBSCRIBERS), 下轮重新等
                    continue
            yield msg
    finally:
        _SUBSCRIBERS.discard(q)


def subscriber_count() -> int:
    return len(_SUBSCRIBERS)
