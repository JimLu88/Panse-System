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
}
COEF_LABELS = {
    "fin_platform_handling_rate": "平台手续费率",
    "fin_platform_activity_rate": "平台活动抽成率",
    "fin_platform_activity_since": "平台活动抽成生效起始日",
    "fin_tax_rate": "税率",
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
