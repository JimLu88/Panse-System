"""订单服务：状态机 + 一些方便的查询。"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from app.models.order import ORDER_STATUS_TRANSITIONS, Order
from app.services import exception_service


class InvalidStatusTransition(ValueError):
    pass


def transition(
    db: Session,
    order: Order,
    target: str,
    *,
    actor: Optional[str] = None,
    force: bool = False,
) -> Order:
    """把订单从当前状态推进到 target。

    非法迁移默认抛 InvalidStatusTransition；force=True 允许跳跃但会同时写一条异常。
    """
    if target == order.status:
        return order
    allowed = ORDER_STATUS_TRANSITIONS.get(order.status, set())
    if target not in allowed:
        if not force:
            raise InvalidStatusTransition(
                f"order {order.order_no}: {order.status!r} → {target!r} 不是合法迁移，"
                f"合法目标: {sorted(allowed)}"
            )
        exception_service.record(
            db,
            source_table="orders",
            source_pk=order.order_no,
            exception_type="forced_status_transition",
            severity="warning",
            description=(
                f"订单 {order.order_no} 被强制从 {order.status} → {target}（绕过状态机）"
                f"{'，操作人: ' + actor if actor else ''}"
            ),
            suggestion_action="review_audit_log",
            context={"from": order.status, "to": target, "actor": actor},
        )
    order.status = target
    db.flush()
    return order
