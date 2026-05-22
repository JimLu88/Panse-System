"""销售统计 + 预测 + 备货建议 (Phase 4, 业务需求 7/8/15/16).

主要 API:
    summary(db, start, end)           — 一段时间内的销售汇总 + 利润排行
    product_breakdown(db, start, end) — 分产品销售明细
    forecast_30d(db)                  — 移动平均预测未来 30 天每个 SKU 销量
    stock_advice(db)                  — 备货建议: 基于预测 + 现库存 + 物料 lead_time

成本估算: 简单方案 — 直接用 Order.theoretical_cost / actual_cost; 都为空时用 0.
对于 historical=True 的订单, 全部跳过 (不入统计)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models.bom import BomLine
from app.models.inventory import PartInventory, ProductInventory
from app.models.material import Material
from app.models.order import Order
from app.models.product import Product


@dataclass
class SalesSummary:
    period_start: date
    period_end: date
    order_count: int = 0
    revenue: Decimal = Decimal("0")
    cost: Decimal = Decimal("0")
    gross_profit: Decimal = Decimal("0")        # revenue - cost - freight 等
    net_profit: Decimal = Decimal("0")           # gross - 安装 - 上楼费 - 补偿
    top_products_by_profit: list[dict] = field(default_factory=list)
    top_products_by_profit_rate: list[dict] = field(default_factory=list)


def _profit_for(o: Order) -> tuple[Decimal, Decimal, Decimal]:
    """返回 (revenue, cost, net_profit)."""
    paid = Decimal(o.paid_amount or 0)
    cost = Decimal(o.actual_cost or o.theoretical_cost or 0)
    freight = Decimal(o.actual_freight or 0)
    upstairs = Decimal(o.upstairs_fee or 0)
    install = Decimal(o.install_fee or 0)
    comp = Decimal(o.compensation_fee or 0)
    net = paid - cost - freight - upstairs - install - comp
    return paid, cost, net


def summary(db: Session, *, start: date, end: date,
            platform: Optional[str] = None) -> SalesSummary:
    """汇总一段时间内已发货/签收订单的销售指标 (业务需求 15)."""
    q = select(Order).where(
        Order.order_date >= start,
        Order.order_date <= end,
        Order.is_historical == False,  # noqa: E712
        Order.status.in_(("paid", "shipped", "signed")),
    )
    if platform:
        q = q.where(Order.platform == platform)
    orders = db.execute(q).scalars().all()

    s = SalesSummary(period_start=start, period_end=end)
    by_product: dict[str, dict] = {}
    for o in orders:
        revenue, cost, net = _profit_for(o)
        s.order_count += 1
        s.revenue += revenue
        s.cost += cost
        s.net_profit += net
        s.gross_profit += revenue - cost
        key = o.product_code or o.product_name or "未知"
        d = by_product.setdefault(key, {
            "product_code": o.product_code,
            "product_name": o.product_name,
            "order_count": 0, "revenue": Decimal("0"),
            "cost": Decimal("0"), "net_profit": Decimal("0"),
        })
        d["order_count"] += 1
        d["revenue"] += revenue
        d["cost"] += cost
        d["net_profit"] += net

    # 利润排行 + 利润率排行
    rows = list(by_product.values())
    for r in rows:
        r["profit_rate"] = (
            (r["net_profit"] / r["revenue"]) if r["revenue"] > 0 else Decimal("0")
        )
    s.top_products_by_profit = sorted(rows, key=lambda r: r["net_profit"], reverse=True)[:10]
    s.top_products_by_profit_rate = sorted(
        rows, key=lambda r: r["profit_rate"], reverse=True,
    )[:10]
    return s


def product_breakdown(
    db: Session, *, start: date, end: date,
) -> list[dict]:
    """分产品 SKU 维度的销售指标 (业务需求 16)."""
    orders = db.execute(
        select(Order).where(
            Order.order_date >= start, Order.order_date <= end,
            Order.is_historical == False,  # noqa: E712
            Order.status.in_(("paid", "shipped", "signed")),
        )
    ).scalars().all()
    by_sku: dict[str, dict] = {}
    for o in orders:
        revenue, cost, net = _profit_for(o)
        key = (o.product_code or "?", o.sku_code or o.sku or "?")
        d = by_sku.setdefault("|".join(key), {
            "product_code": o.product_code, "product_name": o.product_name,
            "sku_code": o.sku_code, "sku": o.sku,
            "qty": 0, "revenue": Decimal("0"),
            "cost": Decimal("0"), "net_profit": Decimal("0"),
        })
        d["qty"] += o.qty or 1
        d["revenue"] += revenue
        d["cost"] += cost
        d["net_profit"] += net
    for r in by_sku.values():
        r["gross_profit_rate"] = (
            ((r["revenue"] - r["cost"]) / r["revenue"]) if r["revenue"] > 0 else Decimal("0")
        )
        r["net_profit_rate"] = (
            (r["net_profit"] / r["revenue"]) if r["revenue"] > 0 else Decimal("0")
        )
    return sorted(by_sku.values(), key=lambda r: r["revenue"], reverse=True)


# ----------------------------- 预测 ----------------------------- #


def _sales_by_day(db: Session, days: int = 90) -> dict[str, dict[date, int]]:
    """过去 N 天每个 SKU 每天的销量. 返回 {sku_key: {date: qty}}.

    key 永远是 'product_code|sku_id' 形式 (无 product_code 用 '?', 无 sku 用 product_code).
    """
    cutoff = date.today() - timedelta(days=days)
    orders = db.execute(
        select(Order).where(
            Order.order_date >= cutoff,
            Order.is_historical == False,  # noqa: E712
            Order.status.in_(("paid", "shipped", "signed")),
        )
    ).scalars().all()
    out: dict[str, dict[date, int]] = {}
    for o in orders:
        if not o.order_date:
            continue
        pc = o.product_code or "?"
        sk = o.sku_code or o.sku or pc
        key = f"{pc}|{sk}"
        out.setdefault(key, {})
        out[key][o.order_date] = out[key].get(o.order_date, 0) + (o.qty or 1)
    return out


def forecast_30d(db: Session) -> list[dict]:
    """业务需求 7 + 8: 简单移动平均预测未来 30 天销量.

    用过去 60 天平均日销 × 30, 加 1.2 倍安全系数。

    返回: [{sku_key, product_code, sku, avg_daily, forecast_30d, last_60d_total}]
    """
    by_sku = _sales_by_day(db, days=60)
    out = []
    for sku_key, day_map in by_sku.items():
        total = sum(day_map.values())
        avg_daily = total / 60
        forecast = int(avg_daily * 30 * 1.2 + 0.5)   # +20% 安全系数
        product_code, _, sku = sku_key.partition("|")
        out.append({
            "sku_key": sku_key,
            "product_code": product_code if product_code != "?" else None,
            "sku": sku,
            "avg_daily": round(avg_daily, 3),
            "forecast_30d": forecast,
            "last_60d_total": total,
        })
    return sorted(out, key=lambda r: r["forecast_30d"], reverse=True)


# ----------------------------- 备货建议 ------------------------- #


def stock_advice(db: Session) -> dict:
    """业务需求 7/8: 智能提前备货建议.

    对每个 SKU:
        - 预测下月销量 (forecast_30d)
        - 现有成品库存 + 已锁定
        - BOM 展开后每个物料 (qty_per_product * forecast) 需要的总量
        - 每个物料对比现库存, 不足部分按 lead_time_days 倒推应在何时下单

    返回:
        {
          "products": [{product_code, sku, forecast_30d, in_stock, need_to_produce}],
          "materials": [{material_code, name, need_qty, have_qty, missing, lead_time_days,
                         alert_at (推荐下单日)}],
        }
    """
    forecast = forecast_30d(db)
    # 收集物料汇总
    material_need: dict[str, Decimal] = {}
    products_out = []
    for f in forecast:
        product_code = f["product_code"]
        if not product_code:
            continue
        # 找成品库存
        pinv = db.execute(
            select(ProductInventory).where(
                ProductInventory.product_code == product_code,
            ).limit(1)
        ).scalar_one_or_none()
        in_stock = pinv.physical_qty if pinv else 0
        need_to_produce = max(f["forecast_30d"] - in_stock, 0)
        products_out.append({
            "product_code": product_code,
            "sku": f["sku"],
            "forecast_30d": f["forecast_30d"],
            "in_stock": in_stock,
            "need_to_produce": need_to_produce,
        })
        if need_to_produce <= 0:
            continue
        # 拉 BOM 倒推每个物料的需求
        bom = db.execute(
            select(BomLine).where(BomLine.product_code == product_code)
        ).scalars().all()
        for line in bom:
            per = Decimal(line.qty_per_product or 0)
            need = (per * Decimal(need_to_produce)).quantize(Decimal("0.001"))
            material_need[line.material_code] = (
                material_need.get(line.material_code, Decimal("0")) + need
            )

    materials_out = []
    today = date.today()
    for mat_code, need in material_need.items():
        mat = db.execute(
            select(Material).where(Material.code == mat_code)
        ).scalar_one_or_none()
        inv = db.execute(
            select(PartInventory).where(PartInventory.material_code == mat_code).limit(1)
        ).scalar_one_or_none()
        have = inv.physical_qty if inv else 0
        missing = float(need) - have
        lead = mat.lead_time_days if mat else 0
        # 假设 30 天后需要交付, lead 天 → 应该在第 (30 - lead) 天前下单. 今天起算:
        alert_at = today + timedelta(days=max(30 - lead, 0))
        materials_out.append({
            "material_code": mat_code,
            "material_name": mat.name if mat else None,
            "need_qty": float(need),
            "have_qty": have,
            "missing": missing,
            "lead_time_days": lead,
            "alert_at": alert_at.isoformat(),
            "should_order_now": missing > 0 and lead >= (30 - (alert_at - today).days),
            "priority": mat.priority if mat else "mid",
        })
    return {"products": products_out, "materials": materials_out}


# ----------------------------- 滞销分类 ------------------------- #


def slow_moving_split(
    db: Session, *,
    long_no_sale_days: int = 60,
    overstock_ratio: float = 3.0,
) -> dict:
    """业务需求 8 滞销分类:
       1) 长期未售: last_outbound 距今 > N 天的成品
       2) 超大库存: 现库存 > overstock_ratio × 未来 30 天预测销量
    """
    today = date.today()
    cutoff = today - timedelta(days=long_no_sale_days)

    # 长期未售: 查 part_inventory.last_outbound_at
    long_idle: list[dict] = []
    rows = db.execute(select(PartInventory).where(PartInventory.physical_qty > 0)).scalars().all()
    for r in rows:
        if r.last_outbound_at and r.last_outbound_at < cutoff:
            long_idle.append({
                "material_code": r.material_code,
                "physical_qty": r.physical_qty,
                "last_outbound_at": r.last_outbound_at.isoformat() if r.last_outbound_at else None,
                "days_since": (today - r.last_outbound_at).days,
            })

    # 超大库存: 比对预测 (按 product_code 聚合, 同 product 多 SKU 求和)
    forecast = forecast_30d(db)
    fmap: dict[str, int] = {}
    for f in forecast:
        if f["product_code"]:
            fmap[f["product_code"]] = fmap.get(f["product_code"], 0) + f["forecast_30d"]
    overstock: list[dict] = []
    pinvs = db.execute(
        select(ProductInventory).where(ProductInventory.physical_qty > 0)
    ).scalars().all()
    for p in pinvs:
        forecast_qty = fmap.get(p.product_code, 0)
        if forecast_qty > 0 and p.physical_qty > overstock_ratio * forecast_qty:
            overstock.append({
                "product_code": p.product_code,
                "sku": p.sku,
                "physical_qty": p.physical_qty,
                "forecast_30d": forecast_qty,
                "ratio": round(p.physical_qty / forecast_qty, 2) if forecast_qty else None,
            })
    return {
        "long_idle": long_idle,
        "overstock": overstock,
        "thresholds": {
            "long_no_sale_days": long_no_sale_days,
            "overstock_ratio": overstock_ratio,
        },
    }
