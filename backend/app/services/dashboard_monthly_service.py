"""数据大盘 · 月度经营 — 工厂口径月度利润/ROI(可切月) + 月度销售占比(饼图)。

用户口径:
  - 只有"付款给工厂"的对账可靠 → 某月「所有工厂都对清(status=balanced)」才标 accurate(准确利润);
    否则 / 无工厂对账数据 → reference_only(未完全核对完成, 仅供参考)。
  - ROI 两条: ① 推广ROI(按月, 复用 roi_service) ② 总投资回收率(逐月累计利润 / 总投资)。
  - 销售占比: 正式销售(非补单/未取消/有日期, 剔除补差价邮费专链), 按 产品 或 店铺 维度。
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance import FactoryReconciliation
from app.models.order import Order
from app.services import cash_flow_service, roi_service, sales_analytics


def _recon_status(db: Session, year: int, month: int) -> dict:
    """该月工厂对账完成度: 取账期与该月有交叠的工厂对账记录, 全 balanced=accurate, 否则 reference_only。"""
    start = date(year, month, 1)
    end = date(year, month, monthrange(year, month)[1])
    statuses = db.execute(
        select(FactoryReconciliation.status).where(
            FactoryReconciliation.period_start <= end,
            FactoryReconciliation.period_end >= start,
        )
    ).scalars().all()
    total = len(statuses)
    if total == 0:
        return {"status": "reference_only", "unbalanced": 0, "total": 0}
    unbalanced = sum(1 for s in statuses if s != "balanced")
    return {
        "status": "accurate" if unbalanced == 0 else "reference_only",
        "unbalanced": unbalanced,
        "total": total,
    }


def _month_list(db: Session, *, cap: int = 36) -> list[tuple[int, int]]:
    """从最早订单月到当前月的 (年,月) 升序列表 (最多 cap 个月)。"""
    earliest = db.execute(select(func.min(Order.order_date))).scalar()
    today = date.today()
    if earliest is None:
        return [(today.year, today.month)]
    y, m = earliest.year, earliest.month
    out: list[tuple[int, int]] = []
    while (y, m) <= (today.year, today.month):
        out.append((y, m))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out[-cap:]


def monthly_pnl(db: Session) -> dict:
    """逐月: 利润(工厂账单口径) + 对账完成度标识 + 推广ROI + 累计投资回收率。最新月在前。"""
    from app.api.reports import _business_month  # 延迟导入避免与 reports 循环

    total_investment = cash_flow_service._get_setting_decimal(
        db, cash_flow_service.SETTING_TOTAL_INVESTMENT, cash_flow_service.DEFAULT_TOTAL_INVESTMENT,
    )
    roi_months = {m["period"]: m for m in roi_service.monthly_breakdown(db)["months"]}

    rows: list[dict] = []
    cumulative = Decimal("0")
    for y, m in _month_list(db):
        bm = _business_month(db, y, m)
        period = f"{y:04d}-{m:02d}"
        net = Decimal(str(bm.get("net_profit") or 0))
        cumulative += net
        recon = _recon_status(db, y, m)
        promo = roi_months.get(period)
        rows.append({
            "period": period,
            "total_revenue": bm.get("total_revenue"),
            "real_revenue": bm.get("real_revenue"),
            "refill_revenue": bm.get("refill_revenue"),
            "total_expense": bm.get("total_expense"),
            "net_profit": bm.get("net_profit"),
            "net_profit_rate": bm.get("net_profit_rate"),
            "recon_status": recon["status"],            # accurate / reference_only
            "unbalanced_factories": recon["unbalanced"],
            "factory_recon_count": recon["total"],
            "promo_roi": promo["roi"] if promo else None,
            "promo_spend": promo["promotion_spend"] if promo else None,
            "promo_spend_ratio": promo["spend_ratio"] if promo else None,
            "cumulative_profit": float(cumulative),
            "recovery_rate": float(cumulative / total_investment) if total_investment else None,
        })
    rows.reverse()
    return {"total_investment": float(total_investment), "rows": rows}


def sales_mix(db: Session, *, year: int, month: int, by: str = "product", top: int = 10) -> dict:
    """某月销售占比 (饼图数据)。by=product|shop; 剔除补差价/邮费/专链非产品。"""
    by = "shop" if by == "shop" else "product"
    start = date(year, month, 1)
    end = date(year, month, monthrange(year, month)[1])
    orders = db.execute(
        select(Order).where(
            Order.is_refill == False,  # noqa: E712
            Order.status != "cancelled",
            Order.order_date >= start,
            Order.order_date <= end,
        )
    ).scalars().all()

    buckets: dict[str, dict] = {}
    total_rev = Decimal("0")
    total_qty = 0
    for o in orders:
        if sales_analytics._is_non_product(o.product_name):
            continue
        if by == "shop":
            name = o.shop or "(未分店)"
        else:
            name = o.product_name or o.product_code or "未知产品"
        rev = Decimal(o.paid_amount or 0)
        qty = int(o.qty or 1)
        b = buckets.setdefault(name, {"name": name, "revenue": Decimal("0"), "qty": 0})
        b["revenue"] += rev
        b["qty"] += qty
        total_rev += rev
        total_qty += qty

    rows = sorted(buckets.values(), key=lambda r: r["revenue"], reverse=True)
    slices = rows[:top]
    rest = rows[top:]
    out_slices = [{
        "name": r["name"], "revenue": float(r["revenue"]), "qty": r["qty"],
        "pct": round(float(r["revenue"] / total_rev * 100), 1) if total_rev else 0.0,
    } for r in slices]
    if rest:
        rest_rev = sum((r["revenue"] for r in rest), Decimal("0"))
        out_slices.append({
            "name": f"其它({len(rest)}种)", "revenue": float(rest_rev),
            "qty": sum(r["qty"] for r in rest),
            "pct": round(float(rest_rev / total_rev * 100), 1) if total_rev else 0.0,
        })
    return {
        "period": f"{year:04d}-{month:02d}", "by": by,
        "total_revenue": float(total_rev), "total_qty": total_qty,
        "slices": out_slices,
    }
