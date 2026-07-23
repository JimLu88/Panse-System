"""月度成品备货计划与飞书幂等推送。"""
from __future__ import annotations

import calendar
import math
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.inventory import ProductInventory
from app.models.order import FactoryOrder
from app.services import (
    feishu_client,
    inventory_demand_service as demand,
    product_coder,
    product_inventory_service,
    settings_service,
)

_SMALL_KW = ("床头柜", "边几", "小桌", "凳", "置物架")
_MEDIUM_KW = ("餐桌", "书桌", "圆桌", "茶桌")
_LARGE_MTO_KW = ("餐边柜", "衣柜", "书柜", "电视柜", "斗柜", "床")


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def _target_period(today: date) -> tuple[int, int]:
    # 月底主任务算下月；1 号兜底任务仍补发当月，避免错推成下下月。
    if today.day == 1:
        return today.year, today.month
    first_next = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first_next.year, first_next.month


def _category_policy(name: str) -> tuple[str, int, int]:
    if "床头柜" in name:
        return "小件热销备货", 7, 6
    if any(k in name for k in _LARGE_MTO_KW):
        return "大件按单生产", 0, 0
    if any(k in name for k in _SMALL_KW):
        return "小件热销备货", 7, 6
    if any(k in name for k in _MEDIUM_KW):
        return "中件少量备货", 5, 2
    return "中件少量备货", 5, 2


def _core(value: Optional[str]) -> str:
    if not value:
        return ""
    return product_coder.core_of(value) or value


def build_monthly_plan(
    db: Session, *, year: int, month: int, as_of: Optional[date] = None,
) -> dict:
    """生成目标月份计划；定制任务和成品备货分开统计。"""
    as_of = as_of or date.today()
    cfg = product_inventory_service.get_forecast_config(db)
    start, end = _month_bounds(year, month)
    standard_profiles = demand.profiles_by_sku(
        db, as_of=as_of, cfg=cfg, kind="standard"
    )
    custom_profiles = demand.profiles_by_sku(
        db, as_of=as_of, cfg=cfg, kind="custom"
    )

    grouped: dict[str, dict] = {}
    for profile in standard_profiles:
        core = profile["product_core"]
        row = grouped.setdefault(core, {
            "product_core": core,
            "product_code": profile["product_code"],
            "product_name": profile["product_name"] or profile["product_code"],
            "normal_daily": 0.0,
            "forecast_month": 0.0,
            "units_90": 0.0,
            "sale_days_90": 0,
            "sku_count": 0,
        })
        row["normal_daily"] += float(profile["normal_daily"])
        row["forecast_month"] += demand.forecast_period(profile, start, end, cfg)
        row["units_90"] += float(profile["window_units"]["90"])
        row["sale_days_90"] += int(profile["sale_days"]["90"])
        row["sku_count"] += 1

    on_hand: dict[str, float] = {}
    for inv in db.execute(select(ProductInventory)).scalars():
        on_hand[_core(inv.product_code)] = (
            on_hand.get(_core(inv.product_code), 0.0)
            + max(0.0, float(inv.available_qty))
        )
    free_in_production: dict[str, float] = {}
    for factory in db.execute(select(FactoryOrder).where(
        FactoryOrder.actual_delivery.is_(None),
        FactoryOrder.voided_at.is_(None),
        FactoryOrder.source_order_id.is_(None),
        FactoryOrder.product_code.isnot(None),
    )).scalars():
        key = _core(factory.product_code)
        free_in_production[key] = (
            free_in_production.get(key, 0.0) + max(0, int(factory.qty or 0))
        )

    products = []
    for core, row in grouped.items():
        policy, buffer_days, cap = _category_policy(row["product_name"])
        qualified = row["units_90"] >= 8
        month_daily = row["forecast_month"] / max(1, (end - start).days + 1)
        target_stock = (
            min(cap, int(math.ceil(month_daily * buffer_days)))
            if qualified and cap > 0
            else 0
        )
        stock = on_hand.get(core, 0.0)
        free = free_in_production.get(core, 0.0)
        suggested = max(0, int(math.ceil(target_stock - stock - free)))
        products.append({
            **row,
            "policy": policy,
            "qualified_hot": qualified,
            "buffer_days": buffer_days,
            "target_stock": target_stock,
            "on_hand": round(stock, 2),
            "free_in_production": round(free, 2),
            "suggested_restock": suggested,
            "forecast_month": round(row["forecast_month"], 2),
            "normal_daily": round(row["normal_daily"], 4),
        })

    custom_tasks = sum(
        demand.forecast_period(profile, start, end, cfg)
        for profile in custom_profiles
    )
    anomaly_orders = {
        item["order_no"]
        for profile in [*standard_profiles, *custom_profiles]
        for item in profile.get("anomalies", [])
    }
    products.sort(
        key=lambda x: (
            -x["suggested_restock"],
            -x["forecast_month"],
            x["product_code"],
        )
    )
    return {
        "period": f"{year:04d}-{month:02d}",
        "generated_on": as_of.isoformat(),
        "products": products,
        "suggested_total": sum(x["suggested_restock"] for x in products),
        "hot_product_count": sum(1 for x in products if x["qualified_hot"]),
        "custom_task_forecast": round(custom_tasks, 1),
        "quantity_anomalies": {"open": len(anomaly_orders)},
        "rules": {
            "windows": [7, 15, 30, 60, 90],
            "promotion_normalization": ["618", "双11", "双12"],
            "cny": "保留春节场景，不压低普通月份基线",
            "hot_threshold_90d": 8,
        },
    }


def format_monthly_plan(plan: dict) -> str:
    restock = [x for x in plan["products"] if x["suggested_restock"] > 0]
    mto = [
        x for x in plan["products"]
        if x["policy"] == "大件按单生产" and x["forecast_month"] > 0
    ]
    lines = [
        f"📦 {plan['period']} 成品备货计划",
        (
            f"建议备货 {plan['suggested_total']} 件｜"
            f"热销达标 {plan['hot_product_count']} 品｜"
            f"预计定制生产任务 {plan['custom_task_forecast']:.1f} 单"
        ),
        "",
    ]
    if restock:
        lines.append("【本月建议备货】")
        for row in restock[:20]:
            lines.append(
                f"• {row['product_code']} {row['product_name'][:18]}："
                f"备 {row['suggested_restock']}，目标 {row['target_stock']}，"
                f"现货 {row['on_hand']:g}，自由在产 {row['free_in_production']:g}"
            )
    else:
        lines.append("【本月建议备货】当前库存与自由在产已覆盖，无需新增成品备货。")
    if mto:
        lines.extend(["", "【大件按单生产，不压成品库存】"])
        for row in mto[:10]:
            lines.append(
                f"• {row['product_code']} {row['product_name'][:18]}："
                f"预计 {row['forecast_month']:.1f} 个生产任务"
            )
    open_anomalies = plan["quantity_anomalies"]["open"]
    if open_anomalies:
        lines.extend([
            "",
            f"⚠️ 数量异常待确认 {open_anomalies} 单（4~5 件提示；>5 件按 1 个定制任务隔离）",
        ])
    lines.extend([
        "",
        "口径：7/15/30/60/90 天近端加权；618/双11/双12去峰后预测；春节数据单列保留。",
    ])
    return "\n".join(lines)


def send_monthly_report(
    db: Session, *, today: Optional[date] = None, force: bool = False,
) -> dict:
    """月底主发 + 次月 1 日兜底共用；成功后才写幂等标记。"""
    today = today or date.today()
    year, month = _target_period(today)
    period = f"{year:04d}-{month:02d}"
    last_period = settings_service.get(
        db, "inventory_monthly_report_last_period", env_fallback=False
    )
    if last_period == period and not force:
        return {"period": period, "pushed": False, "skipped": "already_sent"}
    chat_id = settings_service.get(db, "feishu_push_chat_id", env_fallback=False)
    if not chat_id:
        raise RuntimeError("未配置 feishu_push_chat_id，月度备货计划未发送")
    demand.sync_quantity_anomalies(
        db, cfg=product_inventory_service.get_forecast_config(db), as_of=today
    )
    plan = build_monthly_plan(db, year=year, month=month, as_of=today)
    result = feishu_client.send_text(db, chat_id, format_monthly_plan(plan))
    settings_service.set_value(
        db,
        "inventory_monthly_report_last_period",
        period,
        description="月度成品备货计划最后成功推送月份",
    )
    settings_service.set_value(
        db,
        "inventory_monthly_report_last_message_id",
        str((result.get("data") or {}).get("message_id") or ""),
        description="月度成品备货计划最后飞书消息ID",
    )
    return {
        "period": period,
        "pushed": True,
        "suggested_total": plan["suggested_total"],
        "hot_product_count": plan["hot_product_count"],
        "custom_task_forecast": plan["custom_task_forecast"],
        "message_id": (result.get("data") or {}).get("message_id"),
    }
