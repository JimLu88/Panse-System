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
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.order import FactoryOrder, Order
from app.services import alert_service, inventory_lock_service

_logger = logging.getLogger("panse.factory_order")

# 工厂订单号前缀 — 业务确认用中文「畔色0001」序列 (工厂习惯)。
FACTORY_ORDER_PREFIX = "畔色"


def next_factory_order_no(db: Session) -> str:
    """生成下一个工厂订单号, 格式 畔色0001 / 畔色0002 ...

    取已有最大序号 +1。只认 前缀+纯数字 的历史号 (旧的 F<订单号> 不参与计数)。
    """
    rows = db.execute(
        select(FactoryOrder.factory_order_no).where(
            FactoryOrder.factory_order_no.like(f"{FACTORY_ORDER_PREFIX}%")
        )
    ).scalars().all()
    max_seq = 0
    plen = len(FACTORY_ORDER_PREFIX)
    for no in rows:
        tail = (no or "")[plen:].strip()
        if tail.isdigit():
            max_seq = max(max_seq, int(tail))
    return f"{FACTORY_ORDER_PREFIX}{max_seq + 1:04d}"


def expected_amount_for(
    db: Session, product_code: Optional[str], sku_code: Optional[str], qty: int = 1,
) -> Optional[Decimal]:
    """产品预期金额 = 定价表「总出厂成本」(factory_cost) × 数量。

    用工厂可比口径 (出厂成本) 而非会计总成本: 工厂账单只含木作/打包/外采,
    与 factory_cost 同口径, 这样 产品预期金额 vs 工厂账单金额 才可直接对账。
    (若业务想用会计总成本对账, 把下面 .factory_cost 改成 .accounting_cost 即可。)

    匹配优先级: sku_code 精确 → product_code 首条。任一缺失或无定价 → 返回 None
    (上层据此标记「成本待补」, 不臆造数字)。
    """
    from app.models.pricing import PricingSku

    row = None
    if sku_code:
        row = db.execute(
            select(PricingSku).where(PricingSku.sku_code == sku_code)
        ).scalar_one_or_none()
    if row is None and product_code:
        row = db.execute(
            select(PricingSku).where(PricingSku.product_code == product_code)
            .order_by(PricingSku.id).limit(1)
        ).scalar_one_or_none()
    if row is None or row.factory_cost is None:
        return None
    return row.factory_cost * Decimal(qty or 1)


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

    fo_no = next_factory_order_no(db)

    fo = FactoryOrder(
        factory_order_no=fo_no,
        platform_order_no=order.order_no,
        factory_name=factory_name or "玉山县博冠家具有限公司",
        order_date=order.order_date or date.today(),
        expected_delivery=(order.order_date or date.today()) + timedelta(days=30),
        product_code=order.product_code,
        sku=order.sku,
        qty=order.qty,
        expected_amount=expected_amount_for(db, order.product_code, order.sku_code, order.qty),
        payment_method="月结",
        carrier=order.carrier,
        tracking_no=order.tracking_no,
        source_order_id=order.id,
    )
    db.add(fo)
    db.flush()

    lock_result = inventory_lock_service.lock_for_factory_order(
        db, fo.id, actor=actor,
    )
    # 时间轴
    from app.services import order_event_service
    order_event_service.record(
        db, order_id=order.id, kind="factory_order_generated",
        actor=actor, summary=f"生成工厂下单单 {fo_no}",
        context={
            "factory_order_id": fo.id, "factory_order_no": fo_no,
            "locked_lines": lock_result.locked_lines,
            "shortages": lock_result.shortages,
        },
    )
    if lock_result.shortages:
        order_event_service.record(
            db, order_id=order.id, kind="inventory_shortage",
            actor="system",
            summary=f"⚠️ {len(lock_result.shortages)} 个物料缺货",
            context={"shortages": lock_result.shortages},
        )
    return fo, lock_result


# --------------- 订单 → 工厂单 批量并入 (用户拍板 2026-06-15) -------- #

# 并入口径: 已付款/已发货/已签收 (待付款客户没付钱多半没下厂→不进; cancelled退款→不进; 补单→不进)
SYNC_FACTORY_ORDER_STATES = ("paid", "shipped", "signed")


def sync_from_orders(db: Session, *, dry_run: bool = False) -> dict:
    """把订单系统里 已付款/已发货/已签收 (非补单非历史) 的订单并入工厂下单表。

    用户拍板 2026-06-15: 工厂下单表 = 手工录入 + 订单系统真实订单, 去重, 便于逐单对工厂账单。
    幂等去重: 订单已有有效工厂单 (source_order_id=订单 或 platform_order_no=订单号) → 跳过。
    新建: 带 source_order_id (防与付款自动建/重跑重复) + 产品/数量 + 推算成本(定价 factory_cost×qty,
          缺则空); factory_bill_amount 留空(待对账), payment_status=unpaid。
          不锁库存 (历史补全/对账用, 不走在产库存流程, 区别于 generate_factory_order_for)。
    Returns {created, skipped, candidates, dry_run}
    """
    covered_no = {
        r[0] for r in db.execute(
            select(FactoryOrder.platform_order_no).where(
                FactoryOrder.platform_order_no.isnot(None), FactoryOrder.voided_at.is_(None))
        ).all() if r[0]
    }
    covered_src = {
        r[0] for r in db.execute(
            select(FactoryOrder.source_order_id).where(
                FactoryOrder.source_order_id.isnot(None), FactoryOrder.voided_at.is_(None))
        ).all() if r[0]
    }
    orders = db.execute(
        select(Order).where(
            Order.is_historical == False,  # noqa: E712
            Order.is_refill == False,      # noqa: E712
            Order.status.in_(SYNC_FACTORY_ORDER_STATES),
        ).order_by(Order.order_date, Order.id)
    ).scalars().all()
    # 起始序号: 一次取 max, 之后内存自增 (避免每行查一次 next_factory_order_no)
    seq = int(next_factory_order_no(db)[len(FACTORY_ORDER_PREFIX):])
    created = skipped = 0
    for o in orders:
        if o.id in covered_src or (o.order_no and o.order_no in covered_no):
            skipped += 1
            continue
        if not dry_run:
            fo = FactoryOrder(
                factory_order_no=f"{FACTORY_ORDER_PREFIX}{seq:04d}",
                platform_order_no=o.order_no,
                source_order_id=o.id,
                factory_name="玉山县博冠家具有限公司",
                order_date=o.order_date,
                product_code=o.product_code,
                product_name=o.product_name,
                sku=o.sku,
                qty=o.qty or 1,
                expected_amount=expected_amount_for(db, o.product_code, o.sku_code, o.qty or 1),
                payment_method="月结",
                payment_status="unpaid",
                remark="由订单自动并入工厂下单表",
            )
            db.add(fo)
            seq += 1
        covered_src.add(o.id)
        if o.order_no:
            covered_no.add(o.order_no)
        created += 1
    if not dry_run:
        db.commit()
    _logger.info("sync_from_orders: created=%d skipped=%d candidates=%d dry_run=%s",
                 created, skipped, len(orders), dry_run)
    return {"created": created, "skipped": skipped, "candidates": len(orders), "dry_run": dry_run}


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
    from app.services import order_event_service
    for fo in rows:
        fo.voided_at = datetime.now(timezone.utc)
        fo.voided_reason = reason or "平台订单取消"
        inventory_lock_service.release_factory_order_lock(
            db, fo.id, actor=actor, reason=fo.voided_reason,
        )
        order_event_service.record(
            db, order_id=order.id, kind="factory_order_voided",
            actor=actor, summary=f"作废工厂单 {fo.factory_order_no}",
            detail=fo.voided_reason,
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
    # Plan F3: 有待处理退款单 → 飞书汇总推送一条 (失败不阻断检查)
    if flagged:
        try:
            from app.services import notify_service
            notify_service.notify(
                db,
                f"17:00 退款检查: {flagged} 笔订单处于售后状态超过 24 小时未取消, 请到订单页确认处理。",
                level="warning", title="畔色 ERP [退款检查]",
            )
        except Exception:  # pragma: no cover
            pass
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
