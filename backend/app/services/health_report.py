"""月度数据健康报告 (plan §12.2).

汇总一个月的:
  - 异常统计 (open vs resolved, by type)
  - 6 条对账规则状态
  - 库存周转 (账面价值)
  - 订单 / 营收 / ROI
  - 数据完整性评分 (0-100)
"""
from __future__ import annotations

from calendar import monthrange
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.exception import DataException
from app.models.finance import AlipayFlow
from app.models.order import Order


@dataclass
class HealthReport:
    period_start: date
    period_end: date
    exceptions: dict[str, Any] = field(default_factory=dict)
    reconciliation: dict[str, Any] = field(default_factory=dict)
    inventory: dict[str, Any] = field(default_factory=dict)
    orders: dict[str, Any] = field(default_factory=dict)
    roi: dict[str, Any] = field(default_factory=dict)
    integrity_score: int = 0
    headlines: list[str] = field(default_factory=list)


def generate(db: Session, year: int, month: int) -> HealthReport:
    if not (1 <= month <= 12):
        raise ValueError("month must be 1..12")
    start = date(year, month, 1)
    end = date(year, month, monthrange(year, month)[1])
    r = HealthReport(period_start=start, period_end=end)

    # -------- 异常 --------
    total_open = db.execute(
        select(func.count(DataException.id)).where(DataException.status == "open")
    ).scalar() or 0
    by_severity = {}
    for sev, cnt in db.execute(
        select(DataException.severity, func.count(DataException.id))
        .where(DataException.status == "open")
        .group_by(DataException.severity)
    ).all():
        by_severity[sev] = int(cnt)
    by_type = {}
    for t, cnt in db.execute(
        select(DataException.exception_type, func.count(DataException.id))
        .where(DataException.status == "open")
        .group_by(DataException.exception_type)
        .order_by(func.count(DataException.id).desc())
        .limit(10)
    ).all():
        by_type[t] = int(cnt)
    r.exceptions = {
        "total_open": int(total_open),
        "by_severity": by_severity,
        "top_types": by_type,
    }

    # -------- 对账 (跑一遍) --------
    from app.services import reconciliation_service
    recon_results = reconciliation_service.run_all(db, record_exceptions=False)
    r.reconciliation = {
        name: {
            "total": res.total_diffs,
            "ok": res.ok_count,
            "warning": res.warning_count,
            "error": res.error_count,
        }
        for name, res in recon_results.items()
    }

    # -------- 库存账面 --------
    inv_result = recon_results["inventory_value"]
    total_diff = next((d for d in inv_result.diffs if d.key == "TOTAL"), None)
    r.inventory = {
        "book_value": str(total_diff.expected) if total_diff and total_diff.expected else "0",
        "items_priced": len([d for d in inv_result.diffs if d.severity == "ok" and d.key != "TOTAL"]),
        "items_missing_price": sum(
            1 for d in inv_result.diffs if d.severity == "warning" and d.key == "TOTAL"
        ),
    }

    # -------- 订单 --------
    from app.services.sales_analytics import settled_sale_clause
    order_q = select(
        func.count(Order.id),
        func.coalesce(func.sum(Order.paid_amount), 0),
    ).where(
        Order.order_date >= start, Order.order_date <= end,
        settled_sale_clause(),     # 真实成交(排待付款/取消/全退) 用户拍板 2026-06-17
        Order.is_refill == False,  # noqa: E712 - 销售额全站剔补单 (用户拍板 2026-06-12)
    )
    cnt, rev = db.execute(order_q).one()
    r.orders = {
        "month_count": int(cnt or 0),
        "month_revenue": str(Decimal(rev or 0).quantize(Decimal("0.01"))),
    }

    # -------- ROI --------
    from app.services import roi_service
    roi = roi_service.compute(db, period_start=start, period_end=end)
    r.roi = {
        "promotion_spend": str(roi.promotion_spend),
        "order_count": roi.order_count,
        "order_revenue": str(roi.order_revenue),
        "roi": str(roi.roi) if roi.roi is not None else None,
    }

    # -------- 完整性评分 --------
    score = 100
    score -= min(40, int(total_open) // 10)  # 每 10 条 open 扣 1 分, 最多扣 40
    score -= sum(res.error_count for res in recon_results.values()) * 2  # 每条对账 error 扣 2
    score -= r.inventory["items_missing_price"] * 5
    r.integrity_score = max(0, score)

    # -------- 头条 --------
    headlines: list[str] = []
    if total_open > 0:
        headlines.append(f"未处理异常 {total_open} 条, 最高严重度: {max(by_severity, default='-')}")
    if r.orders["month_count"]:
        headlines.append(f"本月 {r.orders['month_count']} 单, 营收 ¥{r.orders['month_revenue']}")
    if roi.roi is not None:
        headlines.append(f"推广 ROI {roi.roi}× (支出 ¥{roi.promotion_spend})")
    if r.integrity_score < 80:
        headlines.append(f"⚠️ 数据完整性评分 {r.integrity_score}/100, 建议处理积压异常")
    r.headlines = headlines

    return r


def to_dict(r: HealthReport) -> dict[str, Any]:
    d = asdict(r)
    d["period_start"] = r.period_start.isoformat()
    d["period_end"] = r.period_end.isoformat()
    return d
