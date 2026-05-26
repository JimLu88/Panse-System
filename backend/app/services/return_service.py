"""退货流程闭环 (Phase 5, 业务需求 9).

流程:
    1) 客户发起退货 → 创建 AfterSales 记录, 填快递单号 (refill_tracking_no 复用)
    2) 系统追踪快递 → 签收时生成 Alert "退货已签收, 待二次确认入库"
    3) 用户检查产品完好 → 调 confirm_return_inbound(...) 把产品 return_in 库存 (整产品入)
       - second_inbound_confirmed = '是' + processed_at = today
       - 用户可后续点 "拆 BOM" 由 inventory_lock_service.disassemble_product_to_parts 拆分
    4) 退货产品有损坏 → 调 mark_return_damaged(...) → 不入库, 留 alert

公开 API:
    create_return(db, order_no, tracking_no, reason, ...)
    mark_received(db, after_sales_id)
    confirm_return_inbound(db, after_sales_id, *, product_code, qty, actor)
    mark_return_damaged(db, after_sales_id, actor, reason)
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.marketing import AfterSales
from app.models.order import Order
from app.services import alert_service, inventory_lock_service

_logger = logging.getLogger("panse.return")


def create_return(
    db: Session, *, order_no: str, reason: str, tracking_no: Optional[str] = None,
) -> AfterSales:
    """创建一条退货记录. 生成 alert 提醒填快递单号 (如还没填)."""
    order = db.execute(select(Order).where(Order.order_no == order_no)).scalar_one_or_none()
    if order is None:
        raise ValueError(f"订单 {order_no} 不存在")
    a = AfterSales(
        platform_order_no=order_no, reason=reason,
        refill_tracking_no=tracking_no, status="pending_return",
    )
    db.add(a)
    db.flush()
    # 同时把订单状态切到 aftersales
    if order.status not in ("aftersales", "cancelled"):
        order.status = "aftersales"
    if not tracking_no:
        alert_service.upsert(
            db, kind="return_missing_tracking", severity="warn",
            title=f"退货单 {a.id} 缺快递单号",
            body=f"订单 {order_no} 退货已发起, 请尽快填写快递单号以便追踪.",
            dedupe_key=f"return_missing_tracking:{a.id}",
            related_url=f"/aftersales?id={a.id}",
            sticky=True,
        )
    return a


def mark_received(db: Session, after_sales_id: int) -> AfterSales:
    """快递追踪到签收 → 生成"等待二次确认入库"alert (不入库)."""
    a = db.get(AfterSales, after_sales_id)
    if a is None:
        raise ValueError("after_sales 不存在")
    a.status = "received_pending_inspection"
    a.processed_at = date.today()
    alert_service.upsert(
        db, kind="return_pending_inspection", severity="warn",
        title=f"退货已签收, 待检查入库: 订单 {a.platform_order_no}",
        body="请到售后页确认产品完好后入库, 或标记损坏不入库.",
        dedupe_key=f"return_pending_inspection:{a.id}",
        related_url=f"/aftersales?id={a.id}",
        sticky=True,
    )
    return a


def confirm_return_inbound(
    db: Session, after_sales_id: int, *,
    product_code: str, sku_code: Optional[str] = None,
    qty: int = 1, actor: str = "user",
) -> AfterSales:
    """业务需求 9: 用户确认完好, 按整产品入库 (不拆 BOM)."""
    a = db.get(AfterSales, after_sales_id)
    if a is None:
        raise ValueError("after_sales 不存在")
    if a.second_inbound_confirmed == "是":
        return a   # 已入过, 幂等
    inventory_lock_service.return_in_product(
        db, product_code=product_code, sku_code=sku_code, qty=qty,
        actor=actor, source_kind="aftersales", source_id=a.id,
        remark=f"售后 #{a.id} 完好入库",
    )
    a.second_inbound_confirmed = "是"
    a.processed_at = date.today()
    a.status = "returned_in_stock"
    # 关闭相关 alert
    alert_service.resolve_by_dedupe(
        db, f"return_pending_inspection:{a.id}",
        resolved_by=actor,
    )
    return a


def mark_return_damaged(
    db: Session, after_sales_id: int, *,
    actor: str = "user", reason: str = "产品损坏不入库",
) -> AfterSales:
    """业务需求 9: 标记退货损坏, 不入库, 留 alert 警示."""
    a = db.get(AfterSales, after_sales_id)
    if a is None:
        raise ValueError("after_sales 不存在")
    a.second_inbound_confirmed = "否"
    a.processed_at = date.today()
    a.status = "damaged_not_inbound"
    a.remark = f"{a.remark or ''}\n[{date.today().isoformat()}] {actor}: {reason}".strip()
    alert_service.upsert(
        db, kind="return_damaged_not_inbound", severity="warn",
        title=f"退货损坏未入库: 订单 {a.platform_order_no}",
        body=reason,
        dedupe_key=f"return_damaged:{a.id}",
        related_url=f"/aftersales?id={a.id}",
        sticky=False,
        auto_resolve_after_minutes=60 * 24 * 30,   # 留 30 天
    )
    # 关闭等待检查 alert
    alert_service.resolve_by_dedupe(
        db, f"return_pending_inspection:{a.id}",
        resolved_by=actor,
    )
    return a
