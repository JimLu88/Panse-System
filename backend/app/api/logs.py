"""运行日志查看 — 让用户在 ERP 界面里直接看最近的后端日志, 排查问题不用敲 docker 命令."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.dependencies import require_role
from app.log_buffer import get_recent
from app.models.auth import User

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("/recent")
def recent_logs(
    limit: int = Query(200, le=2000),
    level: Optional[str] = Query(None, description="最低级别: INFO/WARNING/ERROR"),
    contains: Optional[str] = Query(None, description="消息关键字过滤"),
    logger_prefix: Optional[str] = Query(None, description="按 logger 名前缀, 如 panse.smart_import"),
    _: User = Depends(require_role("admin", "operator")),
):
    """返回最近的后端运行日志 (内存环形缓冲, 重启后清空)."""
    return {"logs": get_recent(limit, level=level, contains=contains,
                               logger_prefix=logger_prefix)}
