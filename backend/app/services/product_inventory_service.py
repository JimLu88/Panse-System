"""成品库存智能分析服务。

功能:
  compute_product_stats   — 按 product_code/sku 从订单历史推算日均销量、提前期等
  refresh_all_inventory   — 批量更新 ProductInventory 表的推算字段 (幂等, 可定时跑)
  get_inventory_with_stats — 返回带计算字段的库存列表 (用于 API 响应)

计算逻辑:
  日均销量 (daily_sales_30d)  = 近 30 天真实订单出货量 / 30
  提前期 (lead_time_days)     = 工厂订单 actual_delivery - order_date 中位数 (天)
  预警线 (reorder_point)      = safety_stock + lead_time_days × daily_sales_30d
  安全库存 (safety_stock)     = 若未手动设置: lead_time_days × daily_sales_30d × 1.5
  库存预警状态 (warning_status):
    critical — available_qty ≤ 0
    danger   — available_qty < reorder_point
    warning  — days_of_stock < slow_moving_threshold / 2 (快用完)
    excess   — days_of_stock > slow_moving_days (滞销)
    ok       — 正常
  备货量推荐 (auto_reorder_qty):
    = max(0, reorder_point × 2 - available_qty)  (补到预警线的 2 倍)
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal
from statistics import median
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.inventory import ProductInventory
from app.models.order import FactoryOrder, Order

_D = Decimal
_ZERO = _D("0")
_DEFAULT_SLOW_MOVING_DAYS = 60


def _compute_daily_sales(db: Session, product_code: str, sku: Optional[str], days: int = 30) -> float:
    """近 N 天真实订单中该 SKU 的日均发货量。"""
    cutoff = date.today() - timedelta(days=days)
    # 订单 product_code 用 "P" 前缀、目录/库存用 "PPS" 前缀 (P25…↔PPS25…) — 按数字主体桥接两种写法
    core = re.sub(r"^[A-Za-z]+", "", product_code) if product_code else ""
    pc_candidates = {product_code, "P" + core, "PPS" + core} if core else {product_code}
    stmt = (
        select(func.coalesce(func.sum(Order.qty), 0))
        .where(
            Order.product_code.in_(pc_candidates),
            Order.is_refill == False,  # noqa: E712  补单不算真实销量
            Order.order_date >= cutoff,
            # 排除 已关闭/取消/未付款 (兼容导入的中文平台状态 + 系统枚举);
            # 不排除 is_historical: 批量导入默认标历史, 但那正是要分析的销售史。
            Order.status.notin_(["cancelled", "pending_payment"]),
            ~Order.status.like("%关闭%"),
            ~Order.status.like("%取消%"),
            ~Order.status.like("%等待买家付款%"),
        )
    )
    if sku:
        stmt = stmt.where(Order.sku == sku)
    total = float(db.execute(stmt).scalar() or 0)
    return round(total / days, 3)


def _compute_lead_time(db: Session, product_code: str) -> Optional[int]:
    """从工厂订单历史推算中位提前期（天）。只用有完整日期的记录。"""
    rows = db.execute(
        select(FactoryOrder.order_date, FactoryOrder.actual_delivery).where(
            FactoryOrder.product_code == product_code,
            FactoryOrder.order_date.isnot(None),
            FactoryOrder.actual_delivery.isnot(None),
            FactoryOrder.voided_at.is_(None),
        )
    ).all()
    if not rows:
        return None
    deltas = [(r.actual_delivery - r.order_date).days for r in rows if r.actual_delivery >= r.order_date]
    if not deltas:
        return None
    return int(median(deltas))


def compute_product_stats(
    db: Session,
    inv: ProductInventory,
) -> dict:
    """计算单条成品库存的所有推算字段，返回 dict 供 API 序列化或写回数据库。"""
    daily = _compute_daily_sales(db, inv.product_code, inv.sku)

    # 提前期：优先使用手动设置值
    lead_time = inv.lead_time_days
    if lead_time is None:
        lead_time = _compute_lead_time(db, inv.product_code)

    slow_days = inv.slow_moving_days or _DEFAULT_SLOW_MOVING_DAYS

    # 安全库存
    safety = float(inv.safety_stock or 0)
    if safety == 0 and lead_time and daily > 0:
        safety = lead_time * daily * 1.5  # 自动推算: 提前期用量 × 1.5

    # 预警线
    if inv.reorder_point is not None:
        reorder_pt = float(inv.reorder_point)
    else:
        reorder_pt = safety + (lead_time or 0) * daily

    available = float(inv.available_qty)

    # 库存天数 (按日均销量折算)
    days_of_stock: Optional[float] = None
    if daily > 0:
        days_of_stock = round(available / daily, 1)

    # 警告状态
    if available <= 0:
        status = "critical"
    elif reorder_pt > 0 and available < reorder_pt:
        status = "danger"
    elif days_of_stock is not None and days_of_stock < slow_days / 2:
        status = "warning"
    elif days_of_stock is not None and days_of_stock > slow_days:
        status = "excess"
    else:
        status = "ok"

    # 推荐备货量
    auto_reorder = max(0.0, reorder_pt * 2 - available) if reorder_pt > 0 else 0.0

    return {
        "daily_sales_30d": daily,
        "lead_time_days_computed": lead_time,
        "safety_stock_computed": round(safety, 2),
        "reorder_point_computed": round(reorder_pt, 2),
        "available_qty": round(available, 2),
        "days_of_stock": days_of_stock,
        "warning_status": status,
        "auto_reorder_qty": round(auto_reorder, 0),
        "slow_moving_days": slow_days,
    }


def refresh_all_inventory(db: Session) -> int:
    """批量把推算出的提前期/安全库存/预警线写回 ProductInventory 表。幂等。"""
    rows = db.execute(select(ProductInventory)).scalars().all()
    updated = 0
    for inv in rows:
        stats = compute_product_stats(db, inv)
        # 只回填「未手动设置」的字段
        if inv.lead_time_days is None and stats["lead_time_days_computed"] is not None:
            inv.lead_time_days = stats["lead_time_days_computed"]
        if inv.safety_stock is None and stats["safety_stock_computed"] > 0:
            inv.safety_stock = _D(str(stats["safety_stock_computed"]))
        if inv.reorder_point is None and stats["reorder_point_computed"] > 0:
            inv.reorder_point = _D(str(stats["reorder_point_computed"]))
        updated += 1
    db.flush()
    return updated
