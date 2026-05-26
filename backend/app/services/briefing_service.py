"""AI 每日经营简报 (Phase 8, Tier 1 #1).

每天 09:00 调度器跑一次. 收集昨日关键数据 → 喂给 AI → 生成一段中文洞察 + 高亮点.

数据源:
    - 昨日 / 上周 销售汇总 (sales_analytics.summary)
    - 库存风险点 (低库存 + 即将断货 forecast vs 库存)
    - 利润亮点 (top 利润率 SKU)
    - 滞销提醒 (slow_moving)
    - 今日的告警 (alert active count)

输出存 DailyBriefing 表 + 同步推企业微信/钉钉群 (复用 notify_service).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.daily_briefing import DailyBriefing
from app.services import (
    alert_service,
    asset_service,
    inventory_alert_service,
    notify_service,
    sales_analytics,
    settings_service,
)
from app.services.ai_provider import AiUnavailable, build_provider

_logger = logging.getLogger("panse.briefing")


_SYSTEM_PROMPT = """你是畔色家具 ERP 的经营顾问。
根据用户提供的昨日业务数据 JSON, 输出一段简短中文简报 (300 字左右), 包含 4 段:

1. **昨日表现**: 销售额 / 订单数 / 利润, 环比上周.
2. **风险点**: 哪些物料即将断货? 哪些订单需要立刻处理?
3. **机会点**: 哪些 SKU 利润率高值得主推? 哪些客户分类应关注?
4. **建议动作**: 给出 1-3 条具体的今日待办.

输出格式: 纯文本, 用中文, 不要 emoji 不要 markdown 标题, 用 "·" 分点。"""


def _gather_data(db: Session, target_date: date) -> dict:
    """收集昨日和前一周的数据."""
    yesterday_start = target_date
    yesterday_end = target_date
    last_week_start = target_date - timedelta(days=7)
    last_week_end = target_date - timedelta(days=1)

    yesterday = sales_analytics.summary(db, start=yesterday_start, end=yesterday_end)
    last_week = sales_analytics.summary(db, start=last_week_start, end=last_week_end)
    forecast = sales_analytics.forecast_30d(db)
    advice = sales_analytics.stock_advice(db)
    slow = sales_analytics.slow_moving_split(db, long_no_sale_days=60, overstock_ratio=3.0)
    assets = asset_service.summary(db)
    alert_counts = alert_service.count_unresolved_by_severity(db)

    def _d(v):
        return float(Decimal(v or 0)) if isinstance(v, (Decimal, int, float)) else v

    return {
        "for_date": target_date.isoformat(),
        "yesterday": {
            "order_count": yesterday.order_count,
            "revenue": _d(yesterday.revenue),
            "cost": _d(yesterday.cost),
            "net_profit": _d(yesterday.net_profit),
            "top_3_by_profit": [
                {"product_code": r["product_code"],
                 "net_profit": _d(r["net_profit"])}
                for r in yesterday.top_products_by_profit[:3]
            ],
        },
        "last_7_days": {
            "order_count": last_week.order_count,
            "revenue": _d(last_week.revenue),
            "net_profit": _d(last_week.net_profit),
        },
        "stock_risks_top5": [
            {"material_code": m["material_code"],
             "missing": m["missing"], "lead_time_days": m["lead_time_days"],
             "should_order_now": m.get("should_order_now")}
            for m in advice["materials"][:5]
        ],
        "slow_moving_count": {
            "long_idle": len(slow["long_idle"]),
            "overstock": len(slow["overstock"]),
        },
        "assets": {
            "total": float(assets.total),
            "formula_diff": float(assets.diff),
        },
        "alerts_pending": alert_counts,
    }


def generate(db: Session, target_date: Optional[date] = None, *,
             push: bool = True) -> DailyBriefing:
    """生成简报 (幂等: 同一天重复生成会更新已有记录)."""
    if target_date is None:
        target_date = date.today() - timedelta(days=1)

    data = _gather_data(db, target_date)
    cfg = settings_service.get_ai_config(db, "diagnose")
    content = ""
    model = "n/a"
    try:
        provider = build_provider(cfg)
        import json
        resp = provider.chat(
            system=_SYSTEM_PROMPT,
            user=json.dumps(data, ensure_ascii=False),
            max_tokens=600,
        )
        content = resp.text.strip()
        model = resp.model
    except AiUnavailable as e:
        _logger.warning("AI 未配置, 生成 fallback 简报: %s", e)
        content = _fallback_text(data)
        model = "fallback"

    existing = db.execute(
        select(DailyBriefing).where(DailyBriefing.for_date == target_date)
    ).scalar_one_or_none()
    if existing is None:
        b = DailyBriefing(
            for_date=target_date, content=content,
            highlights_json=_extract_highlights(data),
            model=model, generated_at=datetime.now(timezone.utc),
        )
        db.add(b)
    else:
        b = existing
        b.content = content
        b.highlights_json = _extract_highlights(data)
        b.model = model
        b.generated_at = datetime.now(timezone.utc)
    db.flush()

    if push:
        try:
            notify_service.notify(
                db, content,
                level="info", title=f"畔色 ERP 经营简报 {target_date.isoformat()}",
            )
        except Exception as e:  # pragma: no cover
            _logger.warning("通知发送失败: %s", e)
    return b


def _fallback_text(data: dict) -> str:
    """AI 不可用时的退化文本."""
    y = data["yesterday"]
    return (
        f"昨日表现 ({data['for_date']}): "
        f"成交 {y['order_count']} 单, 营收 ¥{y['revenue']:.2f}, "
        f"净利 ¥{y['net_profit']:.2f}. "
        f"未处理告警: critical {data['alerts_pending']['critical']}, "
        f"warn {data['alerts_pending']['warn']}. "
        f"备货风险物料 {len(data['stock_risks_top5'])} 个. "
        f"滞销物料 {data['slow_moving_count']['long_idle']} 个 / "
        f"超大库存 {data['slow_moving_count']['overstock']} 个."
    )


def _extract_highlights(data: dict) -> list[dict]:
    out = []
    if data["alerts_pending"]["critical"] > 0:
        out.append({
            "kind": "risk", "title": f"{data['alerts_pending']['critical']} 个紧急告警待处理",
            "url": "/", "level": "critical",
        })
    for m in data["stock_risks_top5"]:
        if m.get("should_order_now"):
            out.append({
                "kind": "risk",
                "title": f"立即下单: {m['material_code']} 缺 {m['missing']}",
                "url": f"/inventory/parts?code={m['material_code']}",
                "level": "warn",
            })
    if data["yesterday"]["order_count"] > 0:
        out.append({
            "kind": "info",
            "title": f"昨日 {data['yesterday']['order_count']} 单, 净利 ¥{data['yesterday']['net_profit']:.0f}",
            "url": "/reports",
            "level": "info",
        })
    return out[:10]


def list_recent(db: Session, limit: int = 14) -> list[DailyBriefing]:
    return list(db.execute(
        select(DailyBriefing).order_by(DailyBriefing.for_date.desc()).limit(limit)
    ).scalars())
