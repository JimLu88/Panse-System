"""内存环形日志缓冲 — 让最近的运行日志能直接在 ERP 界面里查看.

容器里的日志平时要 `docker compose logs api` 才看得到, 对非技术用户不友好。
这里挂一个 logging.Handler 把最近 N 条日志留在内存, 通过 /api/logs/recent 暴露,
前端可以一键查看「刚才那次操作到底发生了什么」。
"""
from __future__ import annotations

import collections
import logging
from typing import Optional

# 最近 3000 条 (够覆盖一次完整导入的全过程)
_BUFFER: collections.deque[dict] = collections.deque(maxlen=3000)


class RingBufferHandler(logging.Handler):
    """把每条日志格式化后塞进内存 deque."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            _BUFFER.append({
                "ts": self.formatter.formatTime(record, self.formatter.datefmt)
                if self.formatter else "",
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
            })
        except Exception:
            pass


def install_ring_buffer(datefmt: str = "%Y-%m-%d %H:%M:%S") -> RingBufferHandler:
    """挂到 root logger. 在 logging.basicConfig 之后调用一次."""
    handler = RingBufferHandler()
    handler.setFormatter(logging.Formatter(datefmt=datefmt))
    logging.getLogger().addHandler(handler)
    return handler


def get_recent(
    limit: int = 200,
    *,
    level: Optional[str] = None,
    contains: Optional[str] = None,
    logger_prefix: Optional[str] = None,
) -> list[dict]:
    """取最近日志. level=最低级别过滤; contains=消息关键字; logger_prefix=按 logger 名前缀."""
    _LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
    min_level = _LEVELS.get((level or "").upper(), 0)
    out = []
    for rec in _BUFFER:
        if min_level and _LEVELS.get(rec["level"], 0) < min_level:
            continue
        if contains and contains not in rec["msg"]:
            continue
        if logger_prefix and not rec["logger"].startswith(logger_prefix):
            continue
        out.append(rec)
    return out[-limit:]
