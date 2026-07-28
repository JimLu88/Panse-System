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
from app.models.product import Product
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


def policy_for_product(name: str) -> tuple[str, bool]:
    """返回 (策略, 是否备成品)。

    用户 2026-07-28 确认：凡是适合备成品的常规产品，都按未来完整周期备货，
    不再用“小件最多6件 / 中件最多2件”的硬上限。大件仍按单生产，避免压货。
    """
    if "床头柜" in name:
        return "30天滚动备货", True
    if any(k in name for k in _LARGE_MTO_KW):
        return "大件按单生产", False
    if any(k in name for k in _SMALL_KW):
        return "30天滚动备货", True
    if any(k in name for k in _MEDIUM_KW):
        return "30天滚动备货", True
    return "30天滚动备货", True


def build_restock_plan(
    db: Session, *, start: date, end: date, as_of: Optional[date] = None,
) -> dict:
    """按指定未来区间生成唯一备货计划。

    最终建议 = 目标成品库存 − 当前可用现货 − 自由在产。
    目标成品库存只由统一需求预测和产品策略产生：
      常规可备产品覆盖传入的完整未来区间（库存页固定未来 30 天），无件数硬上限；
      没有销量的产品仍建库存行并显示 0，不凭空建议备货；
      大件/定制不推动成品库存，继续按单生产。
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

    # 先把产品主表全量铺进计划。没有销售的产品也必须有库存行和一条 0 计划，
    # 页面不再出现“没建库存行”的第二世界。
    grouped: dict[str, dict] = {}
    for product in db.execute(select(Product).order_by(Product.code)).scalars():
        core = _core(product.code)
        grouped.setdefault(core, {
            "product_core": core,
            "product_code": product.code,
            "product_name": product.name or product.code,
            "normal_daily": 0.0,
            "forecast_period": 0.0,
            "units_90": 0.0,
            "sale_days_90": 0,
            "sales_qty_30d": 0.0,
            "sales_amount_30d": 0.0,
            "actual_daily_30d": 0.0,
            "sku_count": 0,
        })
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
            "sales_qty_30d": 0.0,
            "sales_amount_30d": 0.0,
            "actual_daily_30d": 0.0,
            "sku_count": 0,
        })
        row["normal_daily"] += float(profile["normal_daily"])
        row["forecast_period"] += demand.forecast_period(profile, start, end, cfg)
        row["units_90"] += float(profile["window_units"]["90"])
        row["sale_days_90"] += int(profile["sale_days"]["90"])
        row["sales_qty_30d"] += float(profile["actual_window_units"]["30"])
        row["sales_amount_30d"] += float(profile["actual_window_sales"]["30"])
        row["actual_daily_30d"] += float(profile["actual_daily_30d"])
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
        policy, stock_finished_goods = policy_for_product(row["product_name"])
        qualified = row["units_90"] >= 8
        target_stock = (
            int(math.ceil(row["forecast_period"]))
            if stock_finished_goods and row["forecast_period"] > 0
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
            "buffer_days": days if stock_finished_goods else 0,
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
            "forecast_30d": int(math.ceil(row["forecast_period"])),
            "normal_daily": round(row["normal_daily"], 4),
            "sales_qty_30d": round(row["sales_qty_30d"], 2),
            "sales_amount_30d": round(row["sales_amount_30d"], 2),
            "actual_daily_30d": round(row["actual_daily_30d"], 4),
        })

    _attach_sku_plans(db, products, cfg=cfg, as_of=as_of)

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
            "coverage_days": days,
            "stockable_products": {"mode": "full_period_forecast", "cap": None},
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


def _allocate_open_factory_by_sku(
    factories: list[FactoryOrder],
    inventory_rows: list[ProductInventory],
) -> tuple[dict[int, float], float]:
    """把未到货工厂单按尺寸口令放回对应 SKU；无法确认规格的量不跨 SKU 抵扣。"""
    allocated = {id(row): 0.0 for row in inventory_rows}
    unmatched = 0.0
    tokens = {
        id(row): product_inventory_service._size_token(row.sku)  # noqa: SLF001
        for row in inventory_rows
    }
    for factory in factories:
        qty = max(0.0, float(factory.qty or 0))
        token = product_inventory_service._size_token(factory.sku)  # noqa: SLF001
        candidates = [
            row for row in inventory_rows
            if token and tokens.get(id(row)) == token
        ]
        if len(candidates) == 1:
            allocated[id(candidates[0])] += qty
        elif len(inventory_rows) == 1:
            allocated[id(inventory_rows[0])] += qty
        else:
            unmatched += qty
    return allocated, unmatched


def _attach_sku_plans(
    db: Session, products: list[dict], *, cfg: dict, as_of: date,
) -> None:
    """把产品目标拆成不可互换的 SKU 目标，并逐 SKU 抵扣库存和自由在产。

    产品总目标仍由唯一需求引擎产生；SKU 目标按各自清洗日均用最大余数法拆分，
    保证 SKU 目标之和严格等于产品目标。推荐备货则逐 SKU 计算后再汇总，禁止
    用 1.8 米的富余库存去抵 1.4/1.6 米的缺口。
    """
    inventory_by_core: dict[str, list[ProductInventory]] = {}
    for inv in db.execute(
        select(ProductInventory).order_by(
            ProductInventory.product_code, ProductInventory.sku, ProductInventory.id
        )
    ).scalars():
        inventory_by_core.setdefault(_core(inv.product_code), []).append(inv)

    free_by_core: dict[str, list[FactoryOrder]] = {}
    allocated_by_core: dict[str, list[FactoryOrder]] = {}
    for factory in db.execute(select(FactoryOrder).where(
        FactoryOrder.actual_delivery.is_(None),
        FactoryOrder.voided_at.is_(None),
        FactoryOrder.product_code.isnot(None),
    )).scalars():
        target = (
            free_by_core if factory.source_order_id is None else allocated_by_core
        )
        target.setdefault(_core(factory.product_code), []).append(factory)

    for product in products:
        core = product["product_core"]
        inventory_rows = inventory_by_core.get(core) or []
        if not inventory_rows:
            product["skus"] = []
            product["sku_rows"] = {}
            continue

        weighted_rows = [
            (
                inv,
                product_inventory_service._compute_daily_sales(  # noqa: SLF001
                    db, inv.product_code, inv.sku, cfg=cfg, as_of=as_of
                ),
            )
            for inv in inventory_rows
        ]
        sku_targets = allocate_product_restock(
            int(product.get("target_stock") or 0), weighted_rows
        )
        free, free_unmatched = _allocate_open_factory_by_sku(
            free_by_core.get(core, []), inventory_rows
        )
        allocated, allocated_unmatched = _allocate_open_factory_by_sku(
            allocated_by_core.get(core, []), inventory_rows
        )

        skus = []
        sku_rows = {}
        for inv, daily in weighted_rows:
            target = int(sku_targets.get(id(inv), 0))
            stock = max(0.0, float(inv.available_qty))
            free_qty = float(free.get(id(inv), 0.0))
            suggested = max(0, int(math.ceil(target - stock - free_qty)))
            sku_row = {
                "inventory_id": inv.id,
                "product_code": inv.product_code,
                "sku": inv.sku,
                "warehouse": inv.warehouse,
                "forecast_daily": round(float(daily), 4),
                "forecast_30d": target,
                "target_stock": target,
                "on_hand": round(stock, 2),
                "in_stock": round(stock, 2),
                "free_in_production": round(free_qty, 2),
                "in_production_free": round(free_qty, 2),
                "allocated_in_production": round(
                    float(allocated.get(id(inv), 0.0)), 2
                ),
                "in_production_allocated": round(
                    float(allocated.get(id(inv), 0.0)), 2
                ),
                "suggested_restock": suggested,
                "need_to_produce": suggested,
            }
            skus.append(sku_row)
            sku_rows[str(inv.id)] = sku_row

        product["skus"] = skus
        product["sku_rows"] = sku_rows
        product["suggested_restock"] = sum(
            row["suggested_restock"] for row in skus
        )
        product["need_to_produce"] = product["suggested_restock"]
        product["free_in_production_unmatched"] = round(free_unmatched, 2)
        product["allocated_in_production_unmatched"] = round(
            allocated_unmatched, 2
        )
