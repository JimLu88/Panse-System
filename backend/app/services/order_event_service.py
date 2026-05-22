"""订单事件时间轴 (Phase 8, Tier 1 #2).

业务: 看一个订单 → 时间线展开所有变化, 不用再翻日志/审计.
所有写入点 (order_service / factory_order_service / return_service / lock_service)
都调 record() 写一条.

写入是"添加不删", 永久审计 trail。
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.order_event import OrderEvent

_logger = logging.getLogger("panse.order_event")


def record(
    db: Session, *, order_id: int, kind: str, summary: str,
    actor: Optional[str] = None, detail: Optional[str] = None,
    context: Optional[dict] = None,
) -> OrderEvent:
    e = OrderEvent(
        order_id=order_id, kind=kind, actor=actor,
        summary=summary, detail=detail, context_json=context,
    )
    db.add(e)
    db.flush()
    return e


def list_for_order(db: Session, order_id: int, *, limit: int = 200) -> list[OrderEvent]:
    return list(db.execute(
        select(OrderEvent).where(OrderEvent.order_id == order_id)
        .order_by(OrderEvent.id.asc()).limit(limit)
    ).scalars())
