"""唯一成品备货计划引擎。

库存页、订单备货页、月度飞书只能调用这里得到最终建议数量，禁止各自再算一套。
"""
from __future__ import annotations

import math
from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.exception import DataException
from app.models.inventory import ProductInventory
from app.models.order import FactoryOrder
from app.services import (
    inventory_demand_service as demand,
    product_coder,
    product_inventory_service,
)

_SMALL_KW = ("床头柜", "边几", "小桌", "凳", "置物架")
_MEDIUM_KW = ("餐桌", "书桌", "圆桌", "茶桌")
_LARGE_MTO_KW = ("餐边柜", "衣柜", "书柜", "电视柜", "斗柜", "床")


def _core(value: Optional[str]) -> str:
    if not value:
        return ""
    return product_coder.core_of(value) or value


def policy_for_product(name: str) -> tuple[str, int, int]:
    """返回 (策略, 目标覆盖天数, 单产品成品库存上限)。"""
    if "床头柜" in name:
        return "小件热销备货", 7, 6
    if any(k in name for k in _LARGE_MTO_KW):
        return "大件按单生产", 0, 0
    if any(k in name for k in _SMALL_KW):
        return "小件热销备货", 7, 6
    if any(k in name for k in _MEDIUM_KW):
        return "中件少量备货", 5, 2
    return "中件少量备货", 5, 2


def build_restock_plan(
    db: Session, *, start: date, end: date, as_of: Optional[date] = None,
) -> dict:
    """按指定未来区间生成唯一备货计划。

    最终建议 = 目标成品库存 − 当前可用现货 − 自由在产。
    目标成品库存只由统一需求预测和大小件策略产生：
      90 天清洗销量 >= 8 才进入热销备货；
      小件覆盖 7 天且单品最多 6 件；
      中件覆盖 5 天且单品最多 2 件；
      大件/定制永远不推动成品库存。
    """
    if end < start:
        raise ValueError("end must be >= start")
    as_of = as_of or date.today()
    cfg = product_inventory_service.get_forecast_config(db)
    standard_profiles = demand.profiles_by_sku(
        db, as_of=as_of, cfg=cfg, kind="standard"
    )
    custom_profiles = demand.profiles_by_sku(
        db, as_of=as_of, cfg=cfg, kind="custom"
    )
    days = (end - start).days + 1

    grouped: dict[str, dict] = {}
    for profile in standard_profiles:
        core = profile["product_core"]
        row = grouped.setdefault(core, {
            "product_core": core,
            "product_code": profile["product_code"],
            "product_name": profile["product_name"] or profile["product_code"],
            "normal_daily": 0.0,
            "forecast_period": 0.0,
            "units_90": 0.0,
            "sale_days_90": 0,
            "sku_count": 0,
        })
        row["normal_daily"] += float(profile["normal_daily"])
        row["forecast_period"] += demand.forecast_period(profile, start, end, cfg)
        row["units_90"] += float(profile["window_units"]["90"])
        row["sale_days_90"] += int(profile["sale_days"]["90"])
        row["sku_count"] += 1

    on_hand: dict[str, float] = {}
    for inv in db.execute(select(ProductInventory)).scalars():
        key = _core(inv.product_code)
        on_hand[key] = on_hand.get(key, 0.0) + max(0.0, float(inv.available_qty))

    free_in_production: dict[str, float] = {}
    allocated_in_production: dict[str, float] = {}
    for factory in db.execute(select(FactoryOrder).where(
        FactoryOrder.actual_delivery.is_(None),
        FactoryOrder.voided_at.is_(None),
        FactoryOrder.product_code.isnot(None),
    )).scalars():
        key = _core(factory.product_code)
        target = (
            free_in_production
            if factory.source_order_id is None
            else allocated_in_production
        )
        target[key] = target.get(key, 0.0) + max(0, int(factory.qty or 0))

    products = []
    for core, row in grouped.items():
        policy, buffer_days, cap = policy_for_product(row["product_name"])
        qualified = row["units_90"] >= 8
        period_daily = row["forecast_period"] / max(1, days)
        target_stock = (
            min(cap, int(math.ceil(period_daily * buffer_days)))
            if qualified and cap > 0
            else 0
        )
        stock = on_hand.get(core, 0.0)
        free = free_in_production.get(core, 0.0)
        allocated = allocated_in_production.get(core, 0.0)
        suggested = max(0, int(math.ceil(target_stock - stock - free)))
        products.append({
            **row,
            "policy": policy,
            "qualified_hot": qualified,
            "buffer_days": buffer_days,
            "target_stock": target_stock,
            "on_hand": round(stock, 2),
            "in_stock": round(stock, 2),
            "free_in_production": round(free, 2),
            "in_production_free": round(free, 2),
            "allocated_in_production": round(allocated, 2),
            "in_production_allocated": round(allocated, 2),
            "suggested_restock": suggested,
            "need_to_produce": suggested,
            "forecast_period": round(row["forecast_period"], 2),
            "forecast_30d": int(round(row["forecast_period"])),
            "normal_daily": round(row["normal_daily"], 4),
        })

    custom_tasks = sum(
        demand.forecast_period(profile, start, end, cfg)
        for profile in custom_profiles
    )
    open_quantity_anomalies = len(
        db.execute(
            select(DataException.id).where(
                DataException.exception_type == "inventory_demand_qty_anomaly",
                DataException.status == "open",
            )
        ).scalars().all()
    )
    products.sort(
        key=lambda x: (
            -x["suggested_restock"],
            -x["forecast_period"],
            x["product_code"],
        )
    )
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "generated_on": as_of.isoformat(),
        "products": products,
        "suggested_total": sum(x["suggested_restock"] for x in products),
        "hot_product_count": sum(1 for x in products if x["qualified_hot"]),
        "custom_task_forecast": round(custom_tasks, 1),
        "quantity_anomalies": {"open": open_quantity_anomalies},
        "rules": {
            "windows": [7, 15, 30, 60, 90],
            "hot_threshold_90d": 8,
            "small": {"buffer_days": 7, "cap": 6},
            "medium": {"buffer_days": 5, "cap": 2},
            "large": {"mode": "mto", "target_stock": 0},
            "promotion_normalization": ["618", "双11", "双12"],
            "cny": "保留春节场景，不压低普通月份基线",
        },
    }


def product_map(plan: dict) -> dict[str, dict]:
    return {row["product_core"]: row for row in plan.get("products", [])}


def allocate_product_restock(
    product_total: int, weighted_rows: list[tuple[object, float]],
) -> dict[int, int]:
    """把产品级建议整数按各库存行的清洗日均分配，保证分配和严格等于产品总数。"""
    if product_total <= 0 or not weighted_rows:
        return {id(row): 0 for row, _ in weighted_rows}
    weights = [max(0.0, float(weight)) for _, weight in weighted_rows]
    total_weight = sum(weights)
    if total_weight <= 0:
        return {
            id(row): product_total if index == 0 else 0
            for index, (row, _) in enumerate(weighted_rows)
        }
    raw = [product_total * weight / total_weight for weight in weights]
    allocated = [int(math.floor(value)) for value in raw]
    remainder = product_total - sum(allocated)
    order = sorted(
        range(len(raw)),
        key=lambda index: raw[index] - allocated[index],
        reverse=True,
    )
    for index in order[:remainder]:
        allocated[index] += 1
    return {
        id(row): allocated[index]
        for index, (row, _) in enumerate(weighted_rows)
    }
