"""销售/经营摘要 → 飞书文字卡片推送 (Plan F7)。

用户拍板: 飞书卡片文字图表 (不做 PNG 渲染) — 分栏数字 + 利润 Top5 + 文字条形图。
scheduler: weekly_mon_0930_sales_report (周一 09:30, 推上周); 月报沿用既有 job。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from app.services import sales_analytics

_logger = logging.getLogger("panse.report_push")

_BAR_FULL = "█"
_BAR_WIDTH = 10


def _bar(value: float, max_value: float) -> str:
    """文字条形图: 按占比画 █, 至少 1 格 (有值时)。"""
    if max_value <= 0 or value <= 0:
        return ""
    n = max(1, round(value / max_value * _BAR_WIDTH))
    return _BAR_FULL * n


def _money(v) -> str:
    return f"¥{round(float(v or 0)):,}"


def build_sales_report(db: Session, *, start: date, end: date, label: str) -> str:
    """生成文字版销售摘要 (周报/月报共用)。"""
    s = sales_analytics.summary(db, start=start, end=end)
    lines = [
        f"📊 {label} ({start} ~ {end})",
        f"单量 {s.order_count} ｜ 销售额 {_money(s.revenue)} ｜ 净利 {_money(s.net_profit)}",
    ]
    rate = (float(s.net_profit) / float(s.revenue) * 100) if s.revenue else 0.0
    lines.append(f"净利率 {rate:.1f}%")
    # 品牌分栏 (F8 维度复用)
    brand_parts = []
    for b, name in (("PS", "畔色"), ("PFG", "孚格")):
        bs = sales_analytics.summary(db, start=start, end=end, brand=b)
        if bs.order_count:
            brand_parts.append(f"{name} {bs.order_count}单/{_money(bs.revenue)}")
    if brand_parts:
        lines.append("品牌: " + " ｜ ".join(brand_parts))
    # 利润 Top5 + 文字条形图
    top = (s.top_products_by_profit or [])[:5]
    if top:
        lines.append("— 利润 Top5 —")
        max_profit = max(float(t["net_profit"]) for t in top) or 1.0
        for t in top:
            name = (t.get("product_name") or t.get("product_code") or "?")[:12]
            p = float(t["net_profit"])
            lines.append(f"{_bar(p, max_profit)} {name} {_money(p)}")
    lines.append(f"(执行周期: {label}, 数据截至 {end})")
    return "\n".join(lines)


def push_weekly_sales(db: Session) -> dict:
    """周一 09:30: 推上周 (周一~周日) 销售周报。"""
    today = date.today()
    last_mon = today - timedelta(days=today.weekday() + 7)
    last_sun = last_mon + timedelta(days=6)
    text = build_sales_report(db, start=last_mon, end=last_sun, label="销售周报")
    ok = False
    try:
        from app.services import notify_service
        ok, _ = notify_service.notify(db, text, level="info", title="畔色 ERP [销售周报]")
    except Exception:  # pragma: no cover - 推送失败不阻断 job
        _logger.warning("销售周报推送失败", exc_info=True)
    return {"ok": bool(ok), "period": f"{last_mon}~{last_sun}",
            "preview": text.splitlines()[1] if "\n" in text else text}
