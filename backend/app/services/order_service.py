"""订单服务：状态机 + 一些方便的查询。

Phase 2 扩展: 状态变化时联动 FactoryOrder + 库存锁定 (`factory_order_service`).
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.models.order import ORDER_STATUS_TRANSITIONS, Order
from app.services import exception_service

_logger = logging.getLogger("panse.order_service")


class InvalidStatusTransition(ValueError):
    pass


def transition(
    db: Session,
    order: Order,
    target: str,
    *,
    actor: Optional[str] = None,
    force: bool = False,
    auto_factory: bool = True,
) -> Order:
    """把订单从当前状态推进到 target。

    非法迁移默认抛 InvalidStatusTransition；force=True 允许跳跃但会同时写一条异常。

    auto_factory=True 时, 业务联动 (Phase 2):
        pending_payment → paid     → 自动生成 FactoryOrder + 锁库存
        * → cancelled              → 释放所有关联 FactoryOrder 的锁定
        paid/shipped → shipped     → 锁定库存转为实际扣减 + 更新 last_outbound_at
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

    prev = order.status
    order.status = target
    db.flush()

    # Phase 8 Tier 1 #2: 写时间轴
    from app.services import order_event_service
    order_event_service.record(
        db, order_id=order.id, kind="status_change",
        actor=actor, summary=f"状态 {prev} → {target}",
        context={"from": prev, "to": target, "force": force},
    )

    if auto_factory and not order.is_historical:
        from app.services import factory_order_service as fos
        try:
            if target == "paid" and prev in ("pending_payment", "cancelled"):
                fos.generate_factory_order_for(db, order, actor=actor or "system")
            elif target == "cancelled":
                fos.cancel_factory_orders_for(
                    db, order, reason=f"订单 {order.order_no} 取消",
                    actor=actor or "system",
                )
            elif target == "shipped":
                fos.ship_factory_orders_for(db, order, actor=actor or "system")
        except Exception as e:  # pragma: no cover
            _logger.exception("订单状态联动失败 (不阻塞状态变更): %s", e)

    return order
