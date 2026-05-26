"""自动生成服务 (B5).

从现有订单反推生成:
  - 工厂下单草稿 (FactoryOrder): 凡是没有对应工厂下单的非历史活跃订单,
    自动创建 payment_status=unpaid 草稿, factory_name/unit_price 留空待补填.

纯函数 + 幂等 + 支持 dry_run. 不做一次性历史搬运.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.order import FactoryOrder, Order
from app.services import exception_service

_log = logging.getLogger("panse.autofill")


def generate_factory_orders(db: Session, *, dry_run: bool = False) -> dict:
    """对每条活跃(非历史、非签收/取消)且无对应工厂下单的订单,生成草稿 FactoryOrder.

    Returns: {created, skipped, dry_run}
    """
    existing_platform_nos: set[str] = {
        row[0]
        for row in db.execute(
            select(FactoryOrder.platform_order_no).where(
                FactoryOrder.platform_order_no.isnot(None)
            )
        ).all()
        if row[0]
    }

    stmt = (
        select(Order)
        .where(
            Order.is_historical == False,  # noqa: E712
            Order.status.notin_(["signed", "cancelled"]),
        )
    )
    orders = db.execute(stmt).scalars().all()

    created, skipped = 0, 0
    for order in orders:
        if order.order_no in existing_platform_nos:
            skipped += 1
            continue
        if not order.product_code:
            exception_service.record(
                db,
                source_table="orders",
                source_pk=str(order.id),
                exception_type="autofill_missing_product_code",
                severity="warning",
                description=(
                    f"订单 {order.order_no} 缺产品编码, 无法自动生成工厂下单草稿。"
                    f"请补填产品编码后重新触发生成。"
                ),
                suggestion_action="fill_product_code",
                context={"order_no": order.order_no},
            )
            skipped += 1
            continue

        if not dry_run:
            fo = FactoryOrder(
                platform_order_no=order.order_no,
                product_code=order.product_code,
                sku=order.sku,
                order_date=order.order_date,
                qty=order.qty or 1,
                payment_status="unpaid",
                factory_name=None,
                remark="自动生成草稿 — 请补填工厂名称及单价后确认",
            )
            db.add(fo)
        created += 1

    if not dry_run and created > 0:
        db.flush()

    _log.info(
        "generate_factory_orders: created=%d skipped=%d dry_run=%s",
        created, skipped, dry_run,
    )
    return {"created": created, "skipped": skipped, "dry_run": dry_run}


def run_all(db: Session, *, dry_run: bool = False) -> dict:
    """一次触发所有自动生成任务."""
    factory = generate_factory_orders(db, dry_run=dry_run)
    return {"factory_orders": factory}
