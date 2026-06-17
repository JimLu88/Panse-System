# -*- coding: utf-8 -*-
"""统一财务口径: 会计总成本 + 利润 (用户拍板 2026-06-17)。全系统唯一来源, 不再各处各算。

会计总成本(每单) = 物理产品成本 + 物流 + 安装/上楼 + 售后 + 平台扣点 + 税费
  - 物理产品成本 = actual_cost(工厂实报, 含木作/打包/外采配件) 否则 theoretical_cost(推算)
  - 物流        = actual_freight
  - 安装/上楼   = install_fee + upstairs_fee
  - 售后        = 本单实际(有则用) 否则 全局均值(预测/缺失时, 用户拍板)
  - 平台扣点    = (实付 − 店铺实收) 若实收>0  否则  实付×(手续费率0.6% + 活动抽成率2%[按生效月])
  - 税费        = 本单 tax 有则用, 否则 实付×税率2%
利润 = 实付 − 退款 − 会计总成本

费率在「管理→财务系数设置」配 (改动 2次警告+密码)。settings key: fin_*。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.order import Order
from app.services import settings_service

# 默认费率 (用户拍板 2026-06-17)
DEFAULTS = {
    "fin_platform_handling_rate": "0.006",        # 平台手续费 0.6%
    "fin_platform_activity_rate": "0.02",         # 平台活动抽成 2% (如有)
    "fin_platform_activity_since": "2026-05-01",  # 活动抽成生效起始日 (5月起有, 1-4月无)
    "fin_tax_rate": "0.02",                       # 税费 2%
    "fin_outsourcing_monthly": "10000",           # 人员外包预估 (无实际录入时, 5月起按此/月预估)
    "fin_outsourcing_est_since": "2026-05-01",    # 人员外包预估生效起始月
}
COEF_LABELS = {
    "fin_platform_handling_rate": "平台手续费率",
    "fin_platform_activity_rate": "平台活动抽成率",
    "fin_platform_activity_since": "平台活动抽成生效起始日",
    "fin_tax_rate": "税率",
    "fin_outsourcing_monthly": "人员外包预估(元/月)",
    "fin_outsourcing_est_since": "人员外包预估生效起始月",
}

# 售后费用字段 (订单总表内冗余列; 缺则用均值)
_AS_FIELDS = ("compensation_fee", "good_review_refund", "second_visit_fee",
              "return_pack_freight", "factory_compensation", "logistics_compensation")


def _d(v) -> Decimal:
    try:
        return Decimal(str(v))
    except Exception:  # noqa: BLE001
        return Decimal("0")


def load_coefficients(db: Session) -> dict:
    """读财务系数 (没配用默认)。返回含解析好的 Decimal/date。"""
    raw = {k: (settings_service.get(db, k, env_fallback=False) or dv) for k, dv in DEFAULTS.items()}
    out = dict(raw)
    out["handling_rate"] = _d(raw["fin_platform_handling_rate"])
    out["activity_rate"] = _d(raw["fin_platform_activity_rate"])
    out["tax_rate"] = _d(raw["fin_tax_rate"])
    try:
        y, m, dd = (int(x) for x in str(raw["fin_platform_activity_since"]).split("-"))
        out["activity_since"] = date(y, m, dd)
    except Exception:  # noqa: BLE001
        out["activity_since"] = date(2026, 5, 1)
    return out


def order_aftersales(o: Order) -> Decimal:
    """本单实际售后费 (各冗余列之和)。"""
    return sum((_d(getattr(o, f, 0)) for f in _AS_FIELDS), Decimal("0"))


def aftersales_avg(db: Session) -> Decimal:
    """全局人均售后费 = 全部售后费 ÷ 订单数 (缺失/预测时填它, 用户拍板"算个总均值")。"""
    sums = db.execute(select(*[func.coalesce(func.sum(getattr(Order, f)), 0) for f in _AS_FIELDS])).one()
    total = sum((_d(x) for x in sums), Decimal("0"))
    cnt = db.execute(select(func.count(Order.id))).scalar() or 0
    return (total / cnt).quantize(Decimal("0.01")) if cnt else Decimal("0")


def physical_cost(o: Order) -> Decimal:
    """物理产品成本 = 工厂实报成本优先, 否则系统推算 (含木作/打包/外采配件)。"""
    return _d(o.actual_cost) if o.actual_cost is not None else _d(o.theoretical_cost)


def platform_deduction(o: Order, coef: dict) -> Decimal:
    """平台扣点: 有店铺实收→实付−实收(真实); 否则 实付×(手续费率+活动抽成率[按生效月])。"""
    paid = _d(o.paid_amount)
    recv = _d(o.shop_received_amount)
    if recv > 0 and recv < paid:
        return paid - recv
    rate = coef["handling_rate"]
    if o.order_date and o.order_date >= coef["activity_since"]:
        rate += coef["activity_rate"]
    return (paid * rate).quantize(Decimal("0.01"))


def order_tax(o: Order, coef: dict) -> Decimal:
    """税费: 本单已填 tax 用它, 否则 实付×税率。"""
    if o.tax is not None:
        return _d(o.tax)
    return (_d(o.paid_amount) * coef["tax_rate"]).quantize(Decimal("0.01"))


def cost_breakdown(o: Order, coef: dict, as_avg: Decimal = Decimal("0")) -> dict:
    """会计总成本逐项明细 (供页面"说明里列明细")。"""
    paid = _d(o.paid_amount)
    phys = physical_cost(o)
    freight = _d(o.actual_freight)
    install = _d(o.install_fee) + _d(o.upstairs_fee)
    asales = order_aftersales(o)
    asales_est = asales == 0
    if asales_est:
        asales = as_avg
    platform = platform_deduction(o, coef)
    tax = order_tax(o, coef)
    total = phys + freight + install + asales + platform + tax
    return {
        "physical": phys, "freight": freight, "install_upstairs": install,
        "aftersales": asales, "aftersales_estimated": asales_est,
        "platform": platform, "tax": tax, "total": total, "paid": paid,
    }


def accounting_cost(o: Order, coef: dict, as_avg: Decimal = Decimal("0")) -> Decimal:
    """会计总成本 (全扣项)。"""
    return cost_breakdown(o, coef, as_avg)["total"]


def net_profit(o: Order, coef: dict, as_avg: Decimal = Decimal("0")) -> Decimal:
    """利润 = 实付 − 退款 − 会计总成本。"""
    return _d(o.paid_amount) - _d(o.refund_amount) - accounting_cost(o, coef, as_avg)


# ── 月度/区间报表共用口径 (月度经营数据 与 经营状况 统一, 用户拍板 2026-06-17) ──────────────
# 退款之外的"额外售后"列: 直接赔付客户/二次上门/返厂打包运费/补发运费/万师傅扣款/好评返。
# 不含 平台内·外售后总成本 与 订单赔付费 —— 那些多半就是已从收入扣过的退款, 再计会重复 (用户 Q2 拍板)。
_AS_EXTRA_FIELDS = ("direct_compensation", "second_visit_fee", "return_pack_freight",
                    "refill_freight", "wanshifu_deduction", "good_review_refund")


def extra_aftersales(db: Session, start: date, end: date) -> Decimal:
    """区间内"退款之外"的额外售后支出合计 (按 processed_at)。两个报表共用, 口径一致。"""
    from app.models.marketing import AfterSales
    cols = [func.coalesce(func.sum(getattr(AfterSales, f)), 0) for f in _AS_EXTRA_FIELDS]
    row = db.execute(
        select(*cols).where(AfterSales.processed_at >= start, AfterSales.processed_at <= end)
    ).one()
    return sum((_d(x) for x in row), Decimal("0"))


def extra_aftersales_by_order(db: Session) -> dict[str, Decimal]:
    """各订单"退款之外"的额外售后合计 (after_sales.platform_order_no → 金额)。逐单核对用。"""
    from app.models.marketing import AfterSales
    cols = [func.coalesce(func.sum(getattr(AfterSales, f)), 0) for f in _AS_EXTRA_FIELDS]
    out: dict[str, Decimal] = {}
    for row in db.execute(
        select(AfterSales.platform_order_no, *cols).group_by(AfterSales.platform_order_no)
    ).all():
        if row[0]:
            out[row[0]] = sum((_d(x) for x in row[1:]), Decimal("0"))
    return out


def _iter_months(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def outsourcing_for_range(db: Session, start: date, end: date, coef: dict) -> tuple[Decimal, bool]:
    """人员外包: 当月有实际录入用实际; 否则自 fin_outsourcing_est_since 起按 fin_outsourcing_monthly/月预估。
    返回 (合计, 是否含预估)。两个报表共用 (用户拍板 2026-06-17: 5月起 ¥10000/月预估)。"""
    from app.models.marketing import OutsourcingExpense
    monthly_est = _d(coef.get("fin_outsourcing_monthly") or "10000")
    try:
        ey, em, _ = (int(x) for x in str(coef.get("fin_outsourcing_est_since") or "2026-05-01").split("-"))
        est_since = date(ey, em, 1)
    except Exception:  # noqa: BLE001
        est_since = date(2026, 5, 1)
    rows = db.execute(
        select(func.to_char(OutsourcingExpense.payment_date, "YYYY-MM"),
               func.coalesce(func.sum(OutsourcingExpense.amount), 0))
        .where(OutsourcingExpense.payment_date >= start, OutsourcingExpense.payment_date <= end)
        .group_by(func.to_char(OutsourcingExpense.payment_date, "YYYY-MM"))
    ).all()
    actual = {ym: _d(amt) for ym, amt in rows}
    total = Decimal("0")
    estimated = False
    for y, m in _iter_months(start, end):
        a = actual.get(f"{y}-{m:02d}", Decimal("0"))
        if a > 0:
            total += a
        elif date(y, m, 1) >= est_since:
            total += monthly_est
            estimated = True
    return total, estimated
