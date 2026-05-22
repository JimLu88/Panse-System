"""平台订单 → 工厂订单 自动化 (Phase 2, 业务需求 2/3/10/11).

流程:
    1) Order.status = 'paid' (客服已确认付款) → generate_factory_order_for(order)
       - 创建 FactoryOrder
       - 调 inventory_lock_service.lock_for_factory_order (锁 BOM 配件)
       - 如缺货, lock_service 已生成 critical Alert
    2) Order.status = 'cancelled' → cancel_factory_orders_for(order)
       - 把对应 FactoryOrder.voided_at 标记 + release_factory_order_lock 释放
    3) Order.status = 'shipped' → consume_factory_orders_for(order)
       - lock_service.consume_for_shipment (physical -= qty, locked -= qty)
    4) 17:00 退款检查任务 → 找 status=aftersales 且 compensation>0 的订单,
       推 Alert 提醒 "尽快取消订单"
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.order import FactoryOrder, Order
from app.services import alert_service, inventory_lock_service

_logger = logging.getLogger("panse.factory_order")


# ----------------------------- 创建 ------------------------------ #


def generate_factory_order_for(
    db: Session, order: Order, *,
    factory_name: Optional[str] = None,
    actor: str = "system",
) -> tuple[FactoryOrder, inventory_lock_service.LockResult]:
    """从 platform Order 生成 FactoryOrder, 并自动锁 BOM 配件库存.

    幂等: 如果该 order 已经有对应 FactoryOrder (source_order_id=order.id), 直接复用。
    """
    existing = db.execute(
        select(FactoryOrder).where(FactoryOrder.source_order_id == order.id)
    ).scalar_one_or_none()
    if existing:
        # 已有, 不重复锁; 返回当前状态
        return existing, inventory_lock_service.LockResult(factory_order_id=existing.id)

    if order.is_historical:
        raise ValueError("历史订单 (is_historical) 不参与库存流程")

    fo_no = f"F{order.order_no}"
    # 避免冲突
    if db.execute(select(FactoryOrder).where(FactoryOrder.factory_order_no == fo_no)).scalar_one_or_none():
        fo_no = f"F{order.order_no}_{order.id}"

    fo = FactoryOrder(
        factory_order_no=fo_no,
        platform_order_no=order.order_no,
        factory_name=factory_name or "默认工厂",
        order_date=date.today(),
        product_code=order.product_code,
        sku=order.sku,
        qty=order.qty,
        source_order_id=order.id,
    )
    db.add(fo)
    db.flush()

    lock_result = inventory_lock_service.lock_for_factory_order(
        db, fo.id, actor=actor,
    )
    return fo, lock_result


# ----------------------------- 取消 / 作废 ----------------------- #


def cancel_factory_orders_for(
    db: Session, order: Order, *, reason: Optional[str] = None,
    actor: str = "system",
) -> int:
    """订单取消时, 把所有关联 FactoryOrder 作废 + 释放锁定库存. 返回作废数."""
    rows = db.execute(
        select(FactoryOrder).where(
            FactoryOrder.source_order_id == order.id,
            FactoryOrder.voided_at.is_(None),
        )
    ).scalars().all()
    n = 0
    for fo in rows:
        fo.voided_at = datetime.now(timezone.utc)
        fo.voided_reason = reason or "平台订单取消"
        inventory_lock_service.release_factory_order_lock(
            db, fo.id, actor=actor, reason=fo.voided_reason,
        )
        n += 1
    return n


def void_factory_order(
    db: Session, factory_order_id: int, *, reason: str,
    actor: str = "system",
) -> Optional[FactoryOrder]:
    """单独作废一个工厂订单 (业务需求 11: 17:00 退款检查生成的作废)."""
    fo = db.get(FactoryOrder, factory_order_id)
    if fo is None or fo.voided_at is not None:
        return fo
    fo.voided_at = datetime.now(timezone.utc)
    fo.voided_reason = reason
    inventory_lock_service.release_factory_order_lock(
        db, fo.id, actor=actor, reason=reason,
    )
    return fo


# ----------------------------- 出货 ----------------------------- #


def ship_factory_orders_for(
    db: Session, order: Order, *, actor: str = "system",
) -> int:
    """订单 status=shipped 时, 把所有关联 FactoryOrder 的锁定库存转为实际扣减."""
    rows = db.execute(
        select(FactoryOrder).where(
            FactoryOrder.source_order_id == order.id,
            FactoryOrder.voided_at.is_(None),
            FactoryOrder.actual_delivery.is_(None),
        )
    ).scalars().all()
    n = 0
    for fo in rows:
        fo.actual_delivery = date.today()
        inventory_lock_service.consume_for_shipment(db, fo.id, actor=actor)
        n += 1
    # 更新 order.last_outbound_at
    order.last_outbound_at = datetime.now(timezone.utc)
    return n


# ----------------------------- 远期订单 (业务需求 10) ------------- #


def create_future_order(
    db: Session, *,
    base_order_no: str,
    activate_at: datetime,
    platform: str = "淘宝",
    product_code: Optional[str] = None,
    sku: Optional[str] = None,
    qty: int = 1,
    customer_name: Optional[str] = None,
    remark: Optional[str] = None,
) -> Order:
    """创建一个远期订单. activate_at 到期时定时任务自动改 status=paid 并触发锁库存.

    在原订单取消时 (选项 A) 调用, 派生 30 天后的新订单。
    """
    o = Order(
        platform=platform,
        order_no=f"{base_order_no}_FUT_{activate_at.strftime('%Y%m%d')}",
        product_code=product_code,
        sku=sku,
        qty=qty,
        customer_name=customer_name,
        status="pending_payment",
        activate_at=activate_at,
        remark=remark or "远期订单 (自动派生)",
    )
    db.add(o)
    db.flush()
    return o


# ----------------------------- 17:00 退款检查 (业务需求 11) ------- #


def check_refund_pending_orders(db: Session) -> dict:
    """每天 17:00 调. 找需要变 cancelled 的退款订单, 生成 sticky Alert 提醒.

    判定:
        - status=aftersales (售后中)
        - compensation_fee > 0 或 remark 含 "退款" "退货"
        - 24 小时内未变 cancelled
    """
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    rows = db.execute(
        select(Order).where(
            Order.status == "aftersales",
            Order.is_historical == False,  # noqa: E712
        )
    ).scalars().all()
    flagged = 0
    for o in rows:
        # 简单启发: compensation_fee > 0 视为退款待处理
        needs = (o.compensation_fee or 0) > 0
        if not needs and o.remark:
            needs = "退款" in (o.remark or "") or "退货" in (o.remark or "")
        if not needs:
            continue
        # 已超过 24h 还在 aftersales → flag
        if o.updated_at and o.updated_at > cutoff:
            continue
        alert_service.upsert(
            db,
            kind="refund_pending",
            severity="warn",
            title=f"退款订单待取消: {o.order_no}",
            body=(f"订单 {o.order_no} 处于售后状态 24 小时以上, "
                  f"金额 {o.compensation_fee or 0}, "
                  f"请确认是否需要取消订单并作废工厂单."),
            dedupe_key=f"refund_pending:{o.order_no}",
            related_url=f"/orders?q={o.order_no}",
            context={"order_id": o.id, "compensation_fee": str(o.compensation_fee or 0)},
            sticky=False,
            # P0 #5: 24h 后自动过期, 下一次 tick 如订单仍在 aftersales 会重新生成
            auto_resolve_after_minutes=60 * 24,
        )
        flagged += 1
    return {"flagged": flagged}


# ----------------------------- 缺快递单号检查 (业务需求 6) -------- #


def check_missing_tracking(db: Session) -> dict:
    """扫 PartPurchase 缺 tracking_no 的, 生成持续弹窗 Alert."""
    from app.models.order import PartPurchase
    from datetime import timedelta
    cutoff = date.today() - timedelta(days=1)
    rows = db.execute(
        select(PartPurchase).where(
            PartPurchase.tracking_no.is_(None),
            PartPurchase.purchase_date <= cutoff,
        )
    ).scalars().all()
    for p in rows:
        alert_service.upsert(
            db, kind="missing_tracking", severity="warn",
            title=f"采购单 {p.purchase_no} 缺快递单号",
            body=f"{p.supplier or '?'} / {p.material_name or '?'} / "
                 f"{p.purchase_date.isoformat() if p.purchase_date else '?'}",
            dedupe_key=f"missing_tracking:{p.purchase_no}",
            related_url=f"/inventory/purchases?q={p.purchase_no}",
            sticky=True,   # 持续弹窗
        )
    return {"missing_tracking_count": len(rows)}
