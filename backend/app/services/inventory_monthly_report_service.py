"""月度成品备货计划与飞书幂等推送。"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.services import (
    feishu_client,
    inventory_demand_service as demand,
    inventory_restock_service,
    product_inventory_service,
    settings_service,
)


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    last = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last)


def _target_period(today: date) -> tuple[int, int]:
    # 月底主任务算下月；1 号兜底任务仍补发当月，避免错推成下下月。
    if today.day == 1:
        return today.year, today.month
    first_next = (today.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first_next.year, first_next.month


def build_monthly_plan(
    db: Session, *, year: int, month: int, as_of: Optional[date] = None,
) -> dict:
    """月报只包装唯一备货引擎，不再另算建议数量。"""
    as_of = as_of or date.today()
    start, end = _month_bounds(year, month)
    plan = inventory_restock_service.build_restock_plan(
        db, start=start, end=end, as_of=as_of
    )
    plan["period"] = f"{year:04d}-{month:02d}"
    for row in plan["products"]:
        row["forecast_month"] = row["forecast_period"]
    return plan


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
