"""配件库存智能分析 — 给配件库存算 可用量/库存天数/低库存预警/补货建议。

口径与成品库存一致 (见 product_inventory_service)。

日均消耗 (daily_sales):
  优先「自动计算」= 近 N 天 Σ(订单数量 × 该 SKU 的 BOM 单产品用量) ÷ N。
  口径与理论成本反推一致 (订单缺 sku_code 时用 SKU 名反查定价表, BOM 按 sku_code 查)。
  某物料近 N 天没有消耗记录 → 回退到导入的 avg_daily_sales (用户手填的备用值), 都没有则 0。

提前期/滞销天数/安全库存 取库内导入值 (用户维护)。

warning_status:
  critical — 可用量 ≤ 0
  danger   — 可用量 < 预警线(reorder_point)
  warning  — 库存天数 < 滞销阈值/2 (快用完)
  excess   — 库存天数 > 滞销天数 (滞销/积压)
  ok       — 正常
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bom import BomLine
from app.models.inventory import PartInventory
from app.models.order import Order
from app.models.pricing import PricingSku

_DEFAULT_SLOW_MOVING_DAYS = 60
_CONSUMPTION_WINDOW_DAYS = 90

# 订单状态里这些词 = 非真实需求 (已关闭/取消/未付款/退款), 不计入消耗。
# 兼容导入的中文平台状态 (交易关闭/等待买家付款…) 与系统枚举 (cancelled/pending_payment)。
_NON_DEMAND_KEYWORDS = ("关闭", "取消", "cancelled", "等待买家付款", "待付款",
                        "pending_payment", "退款成功", "已退款")


def _counts_as_demand(status) -> bool:
    s = str(status or "")
    if not s:
        return True   # 状态为空 → 保守计入
    return not any(k in s for k in _NON_DEMAND_KEYWORDS)


def _pc_core(pc) -> Optional[str]:
    """产品编码数字主体 (去前导字母): 订单 P25… / 目录 PPS25… → 25…, 桥接两种前缀。"""
    if not pc:
        return None
    core = re.sub(r"^[A-Za-z]+", "", str(pc).strip())
    return core or None


def compute_material_daily_consumption(db: Session, days: int = _CONSUMPTION_WINDOW_DAYS) -> dict[str, float]:
    """近 N 天各物料的日均消耗 = Σ(订单数量 × 该SKU的BOM单产品用量) ÷ N。

    只算真实出货订单 (shipped/signed、非补单、非历史)。补单不耗真实库存, 排除。
    预取 SKU→sku_code 映射与 BOM, 全程 3 条查询, 不逐单查库。
    返回 {material_code: 日均消耗}; 近 N 天无消耗的物料不在结果里。
    """
    cutoff = date.today() - timedelta(days=days)

    # 1) SKU 名 → sku_code (订单多无 sku_code, 用 SKU 名反查, 与理论成本反推同口径)
    sku_map: dict[str, str] = {}
    for sku, code in db.execute(
        select(PricingSku.sku, PricingSku.sku_code).where(PricingSku.sku.isnot(None))
    ).all():
        if sku and code:
            sku_map[sku] = code

    # 2) sku_code → [(material_code, 单产品用量)]
    bom_by_sku: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for mc, sc, qper in db.execute(
        select(BomLine.material_code, BomLine.sku_code, BomLine.qty_per_product).where(
            BomLine.sku_code.isnot(None)
        )
    ).all():
        if mc and sc:
            bom_by_sku[sc].append((mc, float(qper or 0)))

    # 2b) product_code → sku_code (该产品在 BOM 里只有一个 SKU 时, 可无歧义回退)
    _prod_skus: dict[str, set] = defaultdict(set)
    for pc, sc in db.execute(
        select(BomLine.product_code, BomLine.sku_code).where(BomLine.sku_code.isnot(None))
    ).all():
        core = _pc_core(pc)
        if core and sc:
            _prod_skus[core].add(sc)
    prod_single = {core: next(iter(s)) for core, s in _prod_skus.items() if len(s) == 1}

    # 3) 近 N 天真实需求订单, 展开 BOM 累加每个物料的消耗。
    # 不排除 is_historical (批量导入默认标历史, 但那正是要分析的销售史);
    # 排除补单 + 已关闭/取消/未付款 (导入的是中文平台状态, 用关键词判断);
    # SKU 解析: 订单 sku_code → SKU名反查定价表 → 产品唯一SKU 回退。
    consumption: dict[str, float] = defaultdict(float)
    for sku_code, sku, product_code, qty, status in db.execute(
        select(Order.sku_code, Order.sku, Order.product_code, Order.qty, Order.status).where(
            Order.is_refill == False,      # noqa: E712  补单(刷单)不耗真实库存
            Order.order_date >= cutoff,
        )
    ).all():
        if not _counts_as_demand(status):
            continue
        sc = sku_code or (sku_map.get(sku) if sku else None) or prod_single.get(_pc_core(product_code))
        if not sc or sc not in bom_by_sku:
            continue
        q = float(qty or 0)
        for mc, qper in bom_by_sku[sc]:
            consumption[mc] += q * qper

    return {mc: round(v / days, 3) for mc, v in consumption.items() if v > 0}


def compute_part_stats(inv: PartInventory, daily_consumption: Optional[float] = None) -> dict:
    """单条配件库存的推算字段 (纯计算)。

    daily_consumption: 由 compute_material_daily_consumption 传入的自动日均消耗;
                       None 时回退到导入的 avg_daily_sales。
    """
    if daily_consumption is not None and daily_consumption > 0:
        daily = float(daily_consumption)
        daily_source = "auto"            # 订单×BOM 自动计算
    else:
        daily = float(inv.avg_daily_sales or 0)
        daily_source = "imported" if daily > 0 else "none"

    lead_time = inv.lead_time_days
    slow_days = inv.slow_moving_days or _DEFAULT_SLOW_MOVING_DAYS
    safety = float(inv.safety_stock or 0)
    if safety == 0 and lead_time and daily > 0:
        safety = lead_time * daily * 1.5
    reorder_pt = safety + (lead_time or 0) * daily
    available = float(inv.available_qty)

    days_of_stock: Optional[float] = round(available / daily, 1) if daily > 0 else None

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

    auto_reorder = max(0.0, reorder_pt * 2 - available) if reorder_pt > 0 else 0.0

    return {
        "available_qty": round(available, 2),
        "daily_sales": daily,
        "daily_source": daily_source,
        "lead_time_days": lead_time,
        "slow_moving_days": slow_days,
        "safety_stock_computed": round(safety, 2),
        "reorder_point_computed": round(reorder_pt, 2),
        "days_of_stock": days_of_stock,
        "warning_status": status,
        "auto_reorder_qty": round(auto_reorder, 0),
    }


def list_with_stats(
    db: Session, *, warehouse: Optional[str] = None, material_code: Optional[str] = None,
    limit: int = 200, offset: int = 0,
) -> list[tuple[PartInventory, dict]]:
    """配件库存列表 + 每条的推算字段 (日均消耗由订单×BOM 自动算)。"""
    consumption = compute_material_daily_consumption(db)
    stmt = select(PartInventory)
    if warehouse:
        stmt = stmt.where(PartInventory.warehouse == warehouse)
    if material_code:
        stmt = stmt.where(PartInventory.material_code == material_code)
    stmt = stmt.order_by(PartInventory.id.desc()).limit(limit).offset(offset)
    rows = db.execute(stmt).scalars().all()
    return [(r, compute_part_stats(r, consumption.get(r.material_code))) for r in rows]
