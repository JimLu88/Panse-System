"""工厂单每日汇总推送 (Feature 1).

功能:
- 找所有已付款 (status='paid') 且没有对应工厂单的订单
- 按产品汇总, 格式化生产通知消息
- 通过 notify_service 推送
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.order import FactoryOrder, Order
from app.services import notify_service

_logger = logging.getLogger("panse.factory_summary")


def daily_summary(db: Session) -> dict:
    """找已付款但无工厂单的订单, 按产品汇总后推送生产通知.

    返回:
        {order_count, product_count, items: [{product_code, product_name, qty}]}
    """
    # 查所有 paid 订单
    paid_orders = db.execute(
        select(Order).where(Order.status == "paid", Order.is_historical == False)  # noqa: E712
    ).scalars().all()

    if not paid_orders:
        return {"order_count": 0, "product_count": 0, "items": []}

    paid_order_ids = [o.id for o in paid_orders]

    # 找已有工厂单的订单 id (非作废)
    existing_factory_order_ids = set(
        db.execute(
            select(FactoryOrder.source_order_id).where(
                FactoryOrder.source_order_id.in_(paid_order_ids),
                FactoryOrder.voided_at.is_(None),
            )
        ).scalars().all()
    )

    # 过滤出没有工厂单的订单
    pending_orders = [o for o in paid_orders if o.id not in existing_factory_order_ids]

    if not pending_orders:
        return {"order_count": 0, "product_count": 0, "items": []}

    # 按 product_code 汇总
    product_map: dict[str, dict] = {}
    for o in pending_orders:
        code = o.product_code or "未知"
        if code not in product_map:
            product_map[code] = {
                "product_code": code,
                "product_name": o.product_name or code,
                "qty": 0,
            }
        product_map[code]["qty"] += (o.qty or 1)

    items = sorted(product_map.values(), key=lambda x: -x["qty"])

    # 格式化消息
    today_str = date.today().strftime("%Y-%m-%d")
    lines = [f"📋 {today_str} 待生产订单汇总", f"共 {len(pending_orders)} 张订单，{len(items)} 个产品", ""]
    for item in items:
        lines.append(f"• {item['product_code']} {item['product_name']}  ×{item['qty']}")

    msg = "\n".join(lines)
    notify_service.notify(db, msg, level="info", title="畔色ERP | 今日待生产订单")

    return {
        "order_count": len(pending_orders),
        "product_count": len(items),
        "items": items,
    }
