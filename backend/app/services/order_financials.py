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
    "fin_refill_commission_rate": "0",            # 刷单(补单)佣金率 (占刷单流水, 默认0=没付佣金)
}
COEF_LABELS = {
    "fin_platform_handling_rate": "平台手续费率",
    "fin_platform_activity_rate": "平台活动抽成率",
    "fin_platform_activity_since": "平台活动抽成生效起始日",
    "fin_tax_rate": "税率",
    "fin_outsourcing_monthly": "人员外包预估(元/月)",
    "fin_outsourcing_est_since": "人员外包预估生效起始月",
    "fin_refill_commission_rate": "刷单佣金率(占刷单流水)",
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


def cost_breakdown(o: Order, coef: dict, as_avg: Decimal = Decimal("0"),
                   aftersales: "Decimal | None" = None) -> dict:
    """会计总成本逐项明细 (供页面"说明里列明细")。
    aftersales 显式传入(按订单归属, 退款外额外售后)时直接用它; 否则回退 本单冗余列→人均均摊(旧)。"""
    paid = _d(o.paid_amount)
    phys = physical_cost(o)
    freight = _d(o.actual_freight)
    install = _d(o.install_fee) + _d(o.upstairs_fee)
    if aftersales is not None:
        asales = _d(aftersales)
        asales_est = False
    else:
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


def accounting_cost(o: Order, coef: dict, as_avg: Decimal = Decimal("0"),
                    aftersales: "Decimal | None" = None) -> Decimal:
    """会计总成本 (全扣项)。"""
    return cost_breakdown(o, coef, as_avg, aftersales)["total"]


def net_profit(o: Order, coef: dict, as_avg: Decimal = Decimal("0"),
               aftersales: "Decimal | None" = None) -> Decimal:
    """利润 = 实付 − 退款 − 会计总成本。"""
    return _d(o.paid_amount) - _d(o.refund_amount) - accounting_cost(o, coef, as_avg, aftersales)


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


_DEFAULT_FIXED_COSTS = [{"name": "房租", "amount": 40000, "period": "yearly", "active": True}]


def fixed_cost_items(db: Session) -> list[dict]:
    """自定义固定成本/管理费用项 (房租/水电/软件/折旧…)。存 setting fin_fixed_cost_items(JSON)。
    未设置过 → 返回默认 [房租 ¥40000/年]; 已设置(哪怕空[]) → 用存的, 这样用户可自由增删 (用户拍板 2026-06-18)。"""
    import json
    raw = settings_service.get(db, "fin_fixed_cost_items", env_fallback=False)
    if raw is None:
        return [dict(x) for x in _DEFAULT_FIXED_COSTS]
    try:
        items = json.loads(raw)
        return items if isinstance(items, list) else []
    except Exception:  # noqa: BLE001
        return []


def fixed_costs_monthly(db: Session) -> Decimal:
    """每月固定成本合计 (年度项 ÷12)。"""
    total = Decimal("0")
    for it in fixed_cost_items(db):
        if not it.get("active", True):
            continue
        amt = _d(it.get("amount"))
        if str(it.get("period")) == "yearly":
            amt = amt / 12
        total += amt
    return total.quantize(Decimal("0.01"))


def refill_cost(db: Session, start: date, end: date, coef: dict) -> dict:
    """补单=刷单 的纯成本 (用户拍板 2026-06-18): 流水本金来回滚抵销(非收入), 真正花出去的是
    平台扣点 + 税费 + 运费 + 佣金。不计商品成本(刷单货回流/不消耗), 不计收入(本金回流)。
    佣金 = 刷单流水(实付) × fin_refill_commission_rate (默认0, 在财务系数设置里填)。"""
    from app.models.order import Order
    orders = db.execute(
        select(Order).where(
            Order.is_refill == True,  # noqa: E712
            Order.order_date >= start, Order.order_date <= end)
    ).scalars().all()
    gmv = platform = tax = freight = Decimal("0")
    for o in orders:
        gmv += _d(o.paid_amount)
        platform += platform_deduction(o, coef)
        tax += order_tax(o, coef)
        freight += _d(o.actual_freight)
    commission = (gmv * _d(coef.get("fin_refill_commission_rate") or "0")).quantize(Decimal("0.01"))
    total = (platform + tax + freight + commission).quantize(Decimal("0.01"))
    return {"count": len(orders), "gmv": gmv.quantize(Decimal("0.01")),
            "platform": platform.quantize(Decimal("0.01")), "tax": tax.quantize(Decimal("0.01")),
            "freight": freight.quantize(Decimal("0.01")), "commission": commission, "total": total}


def accounting_summary(db: Session, start: date, end: date) -> dict:
    """全系统统一会计 P&L (用户拍板 2026-06-18: 月度经营/经营状况/逐单核对/销售汇总/大盘 同口径)。

    收入 = Σ(实付 − 退款)  [真实成交、非补单]
    逐单成本 = 商品 + 物流 + 安装上楼 + 平台扣点(实付−实收) + 税 + 额外售后(按订单归属, 退款不重复计)
    区间成本 = 推广 + 人员外包 + 固定成本(房租等) + 补单(刷单)成本
    净利 = 收入 − 逐单成本 − 区间成本
    退款≠售后: 退款已从收入扣过, 售后只算退款之外的额外赔付。
    """
    from app.models.marketing import PromotionFlow
    from app.models.order import Order
    from app.services import sales_analytics
    coef = load_coefficients(db)
    as_by_order = extra_aftersales_by_order(db)
    orders = db.execute(
        select(Order).where(
            Order.order_date >= start, Order.order_date <= end,
            sales_analytics.settled_sale_clause(), Order.is_refill == False)  # noqa: E712
    ).scalars().all()
    revenue = refund = goods = freight = install = platform = tax = aftersales = Decimal("0")
    goods_est = False
    as_count = 0
    for o in orders:
        rf = _d(o.refund_amount)
        revenue += _d(o.paid_amount) - rf
        refund += rf
        b = cost_breakdown(o, coef, Decimal("0"))   # as_avg=0: 不用人均均摊, 售后改按订单
        goods += b["physical"]; freight += b["freight"]; install += b["install_upstairs"]
        platform += b["platform"]; tax += b["tax"]
        _as = _d(as_by_order.get(o.order_no, 0))
        aftersales += _as
        if _as > 0:
            as_count += 1
        if o.actual_cost is None:
            goods_est = True   # 用推演商品成本(工厂未对账)
    promo = _d(db.execute(
        select(func.coalesce(func.sum(PromotionFlow.amount), 0)).where(
            PromotionFlow.flow_type == "支出",
            PromotionFlow.transaction_date >= start, PromotionFlow.transaction_date <= end)
    ).scalar() or 0)
    outsourcing, os_est = outsourcing_for_range(db, start, end, coef)
    fixed = fixed_costs_monthly(db)
    rc = refill_cost(db, start, end, coef)
    order_cost = goods + freight + install + platform + tax + aftersales
    period_cost = promo + outsourcing + fixed + rc["total"]
    net = revenue - order_cost - period_cost
    return {
        "count": len(orders), "revenue": revenue, "refund": refund,
        "goods": goods, "goods_estimated": goods_est,
        "freight": freight, "install": install, "platform": platform,
        "tax": tax, "aftersales": aftersales, "aftersales_count": as_count, "order_cost": order_cost,
        "promo": promo, "outsourcing": outsourcing, "outsourcing_estimated": os_est,
        "fixed": fixed, "refill": rc, "period_cost": period_cost,
        "total_cost": order_cost + period_cost, "net": net,
        "net_margin": (net / revenue * 100) if revenue else Decimal("0"),
        "coef": coef,
    }


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
