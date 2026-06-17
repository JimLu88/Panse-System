"""分店统计 (Task 7): 按 shop(店铺) 聚合订单 单数 / 销量 / 销售额。

店铺来源: 订单导入时经对应表 skuId/编码 反查得到 (taobao_order_import + taobao_listing_service)。
同一实物可跨店上架, 各店各自计入自己的 shop。未归属(shop 为空)单独成一桶。
排除: 已取消/关闭订单、补单 (除非显式包含)。
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.order import Order


def compute_shop_stats(
    db: Session,
    *,
    start: Optional[date] = None,
    end: Optional[date] = None,
    include_refill: bool = False,
) -> list[dict]:
    """返回 [{shop, order_count, total_qty, total_revenue}], 按销售额降序。"""
    from app.services.sales_analytics import settled_sale_clause
    stmt = (
        select(
            Order.shop,
            func.count(Order.id),
            func.coalesce(func.sum(Order.qty), 0),
            func.coalesce(func.sum(Order.paid_amount), 0),
        )
        .where(settled_sale_clause())   # 统一成交口径: 排待付款/取消/关闭/全额退款 (用户拍板 2026-06-17)
    )
    if not include_refill:
        stmt = stmt.where(Order.is_refill == False)  # noqa: E712
    if start is not None:
        stmt = stmt.where(Order.order_date >= start)
    if end is not None:
        stmt = stmt.where(Order.order_date <= end)
    stmt = stmt.group_by(Order.shop)

    rows = db.execute(stmt).all()
    out = [
        {
            "shop": shop or "未归属",
            "order_count": int(cnt or 0),
            "total_qty": int(qty or 0),
            "total_revenue": float(rev or 0),
        }
        for shop, cnt, qty, rev in rows
    ]
    out.sort(key=lambda x: x["total_revenue"], reverse=True)
    return out
