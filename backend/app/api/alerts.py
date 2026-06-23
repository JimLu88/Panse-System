"""告警 / 通知中心 API (Phase 1B + Phase 12 SSE).

GET  /api/alerts/active                  前端 NotificationBell 轮询 (每 30s)
GET  /api/alerts/summary                 角标用 (按 severity 计数)
POST /api/alerts/{id}/dismiss            手动 resolve (sticky 也允许, 业务层应确保根因已修)
GET  /api/alerts/history                 历史告警列表 (含 resolved)
GET  /api/alerts/stream                  SSE 推送 (Phase 12): 实时新告警
"""
from __future__ import annotations

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.models.alert import Alert
from app.models.auth import User
from app.services import alert_service, sse_bus

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


class AlertOut(BaseModel):
    id: int
    kind: str
    severity: str
    title: str
    body: Optional[str]
    dedupe_key: Optional[str]
    related_url: Optional[str]
    context_json: Optional[dict]
    sticky: bool
    resolved_at: Optional[str]
    resolved_by: Optional[str]
    auto_resolve_until: Optional[str]
    notified_at: Optional[str]
    created_at: str


def _out(a: Alert) -> AlertOut:
    return AlertOut(
        id=a.id, kind=a.kind, severity=a.severity, title=a.title, body=a.body,
        dedupe_key=a.dedupe_key, related_url=a.related_url,
        context_json=a.context_json, sticky=a.sticky,
        resolved_at=a.resolved_at.isoformat() if a.resolved_at else None,
        resolved_by=a.resolved_by,
        auto_resolve_until=a.auto_resolve_until.isoformat() if a.auto_resolve_until else None,
        notified_at=a.notified_at.isoformat() if a.notified_at else None,
        created_at=a.created_at.isoformat(),
    )


@router.get("/active", response_model=list[AlertOut])
def get_active(
    severity: Optional[str] = None, kind: Optional[str] = None,
    limit: int = 200,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    rows = alert_service.list_active(db, severity=severity, kind=kind, limit=limit)
    return [_out(a) for a in rows]


class AlertSummary(BaseModel):
    info: int
    warn: int
    critical: int


@router.get("/summary", response_model=AlertSummary)
def get_summary(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    return AlertSummary(**alert_service.count_unresolved_by_severity(db))


class DismissOut(BaseModel):
    id: int
    resolved_at: Optional[str]
    resolved_by: Optional[str]


@router.post("/{alert_id}/dismiss", response_model=DismissOut)
def dismiss(
    alert_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    a = alert_service.resolve(
        db, alert_id, resolved_by=getattr(user, "username", "user"),
    )
    if a is None:
        raise HTTPException(404, "告警不存在")
    db.commit()
    return DismissOut(
        id=a.id,
        resolved_at=a.resolved_at.isoformat() if a.resolved_at else None,
        resolved_by=a.resolved_by,
    )


@router.get("/history", response_model=list[AlertOut])
def get_history(
    limit: int = 100,
    kind: Optional[str] = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    q = select(Alert).order_by(Alert.id.desc()).limit(limit)
    if kind:
        q = q.where(Alert.kind == kind)
    return [_out(a) for a in db.execute(q).scalars()]


# ----------------------------- SSE 推送 (Phase 12) ---------------- #


@router.get("/stream")
async def alert_stream(request: Request):
    """Server-Sent Events: 客户端订阅, 服务端实时推送 alert / order event.

    替代 30s 轮询. EventSource(/api/alerts/stream) 接.
    用 stream 后, NotificationBell 不再需要 refetchInterval.
    """
    async def event_gen():
        # heartbeat 注释 (浏览器/代理保活)
        yield ": connected\n\n"
        try:
            async for msg in sse_bus.subscribe(heartbeat_s=25.0):
                if await request.is_disconnected():
                    break
                if msg.get("event") == "_heartbeat":
                    # 25s 保活注释: 连接每 25s 有数据流动 < nginx proxy_read_timeout(120s),
                    # 不再被当超时断开 → 前端不再每 ~120s 重连一次 (砍掉日志/磁盘琐碎写入, NAS 盘能睡)
                    yield ": ping\n\n"
                    continue
                event_name = msg.get("event", "message")
                data = json.dumps(msg.get("data", {}), ensure_ascii=False)
                yield f"event: {event_name}\ndata: {data}\n\n"
        except asyncio.CancelledError:
            pass

    return StreamingResponse(
        event_gen(), media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # nginx 不缓冲
            "Connection": "keep-alive",
        },
    )
