"""工厂对账单生成 (用户需求 2026-06-16): 按月自动生成「给工厂的对账单」。

每行 = 一张订单: 售价(实收) / 预测工厂价 / 盈亏平衡工厂价(净不亏红线) / 安全垫。
口径 (与定制报价盈亏平衡一致, 用户拍板「净不亏」):
  预测工厂价   = 定价表 factory_cost × 数量 (缺则回退订单实际成本/定制加价, 标注待补)
  盈亏平衡价   = 售价 − 非工厂成本((accounting_cost − factory_cost) × 数量)
               (accounting_cost 已含运费/安装/税/平台扣点 → 这是真·净不亏红线)
  安全垫       = 盈亏平衡价 − 预测价 = 售价 − accounting_cost×数量 (≈本单利润)
工厂实际报价 ≤ 红线 才不亏; 逼近/超红线 = 利润被吃光/亏本, 该找工厂谈。

只读纯计算 (不写任何表)。排除 补单/零成本(安装/官方服务)/无产品 的订单。
"""
from __future__ import annotations

import calendar
from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.order import Order
from app.models.pricing import PricingSku
from app.services import order_cost_service


def _month_range(period: Optional[str]) -> Optional[tuple[date, date]]:
    """'YYYY-MM' → (月初, 月末); 非法/空 → None。"""
    if not period:
        return None
    try:
        y, m = (int(x) for x in period.split("-")[:2])
        last = calendar.monthrange(y, m)[1]
        return date(y, m, 1), date(y, m, last)
    except (ValueError, IndexError):
        return None


def _month_key(d: Optional[date]) -> Optional[str]:
    return f"{d.year}-{d.month:02d}" if d else None


def available_periods(db: Session, *, limit: int = 36) -> list[str]:
    """有订单的月份(YYYY-MM)倒序, 给前端月份选择器。"""
    dates = db.execute(
        select(Order.order_date).where(Order.order_date.isnot(None))
    ).scalars().all()
    months = sorted({_month_key(d) for d in dates if d}, reverse=True)
    return months[:limit]


def _excluded_reason(order: Order) -> Optional[str]:
    """该单是否排除出工厂对账单(补单/零成本服务/无产品)→ 原因; 否则 None。"""
    if order.is_refill:
        return "补单"
    if not order.product_code:
        return "无产品"
    zc = order_cost_service.zero_cost_reason(order)
    if zc:
        return zc
    return None


# 估算回落时区分"工厂/产品成本"与"费用"(费用不算进预测工厂价)
_EST_FEE_CODES = {"运费", "安装", "税费", "扣点"}


def _estimate_predicted(db: Session, order: Order) -> tuple[Optional[float], float, Optional[str]]:
    """定价表查不到工厂价 → 用 order_cost_service 估算 (BOM反推/同款均价)。

    返回 (预测工厂价估算[per order], 费用[per unit: 运费+安装+税+扣点], compute备注)。
    取不到 → (None, 0, None)。标注由调用方加「估算」。
    """
    try:
        from app.services import order_cost_service
        bd = order_cost_service.compute(db, order)
    except Exception:  # noqa: BLE001
        return None, 0.0, None
    qty = int(order.qty or 1)
    non_fee = sum(float(ln.line_cost) for ln in bd.lines
                  if ln.line_cost is not None and ln.material_code not in _EST_FEE_CODES)
    fee_unit = sum(float(ln.line_cost) for ln in bd.lines
                   if ln.line_cost is not None and ln.material_code in _EST_FEE_CODES)
    if non_fee <= 0:
        return None, 0.0, None
    return round(non_fee * qty, 2), fee_unit, bd.note


def generate(db: Session, *, period: Optional[str] = None, limit: int = 5000) -> dict:
    """生成工厂对账单。period='YYYY-MM' 按 order_date 筛; None=全部(限 limit)。

    返回 {period, count, totals, rows[]}; rows 每条含 预测/平衡/安全垫 + missing 标注。
    """
    rng = _month_range(period)
    stmt = select(Order).order_by(Order.order_date.desc().nulls_last(), Order.id.desc())
    if rng:
        stmt = stmt.where(Order.order_date >= rng[0], Order.order_date <= rng[1])
    orders = db.execute(stmt).scalars().all()
    # 样品出货单不进工厂对账单 (样品早做好、不向工厂下单; 修复费/转运费走配件采购)
    from app.models.marketing import Sample
    sample_order_nos = {
        no for (no,) in db.execute(
            select(Sample.related_order_no).where(Sample.related_order_no.isnot(None))
        ).all() if no
    }
    orders = [o for o in orders
              if _excluded_reason(o) is None and o.order_no not in sample_order_nos][:limit]

    # 预加载 PricingSku (防 N+1): 先按 sku_code, 无 code 的按 sku 名
    codes = {o.sku_code for o in orders if o.sku_code}
    ps_by_code = {
        ps.sku_code: ps for ps in db.execute(
            select(PricingSku).where(PricingSku.sku_code.in_(codes))
        ).scalars()
    } if codes else {}
    skus = {o.sku for o in orders if not o.sku_code and o.sku}
    ps_by_sku = {
        ps.sku: ps for ps in db.execute(
            select(PricingSku).where(PricingSku.sku.in_(skus))
        ).scalars()
    } if skus else {}

    from app.services import custom_quote_config_service as ccfg
    cfg = ccfg.get_config(db)
    plat = float(cfg.get("platform_fee_rate", 0.05))
    tax = float(cfg.get("tax_rate", 0.0))

    rows: list[dict] = []
    tot_revenue = tot_predicted = tot_break_even = tot_buffer = 0.0
    missing = 0
    for o in orders:
        ps = ps_by_code.get(o.sku_code) or (ps_by_sku.get(o.sku) if o.sku else None)
        qty = int(o.qty or 1)
        revenue = float(o.shop_received_amount if o.shop_received_amount is not None
                        else (o.buyer_payable_amount or 0) or 0)
        fac_unit = float(ps.factory_cost) if (ps and ps.factory_cost is not None) else None
        acc_unit = float(ps.accounting_cost) if (ps and ps.accounting_cost is not None) else None

        note = None
        estimated = False
        est_fee_unit = 0.0
        if fac_unit is not None:
            predicted = round(fac_unit * qty, 2)
        elif o.actual_cost is not None:
            predicted = round(float(o.actual_cost), 2)
            note = "缺定价表工厂价 → 用订单实际成本"
        elif o.custom_surcharge is not None:
            predicted = round(float(o.custom_surcharge), 2)
            note = "定制单 → 用定制加价占位"
        else:
            predicted, est_fee_unit, est_note = _estimate_predicted(db, o)
            if predicted is not None:
                estimated = True
                note = "估算: " + (est_note or "定价表无工厂价 → 成本反推/同款均价")
            else:
                note = "缺工厂价(待补定价表/实际成本)"

        break_even = buffer = None
        if revenue and fac_unit is not None and acc_unit is not None:
            non_factory = (acc_unit - fac_unit) * qty
            break_even = round(revenue - non_factory, 2)
            buffer = round(break_even - predicted, 2) if predicted is not None else None
        elif revenue and estimated and predicted is not None:
            # 估算单: 红线 = 实收×(1−平台扣点−税) − 估算运费/安装 (保守, 守只高不低)
            break_even = round(revenue * (1 - plat - tax) - est_fee_unit * qty, 2)
            buffer = round(break_even - predicted, 2)
        elif not revenue:
            note = (note + "; " if note else "") + "缺售价(实收)→ 无法算红线"

        rows.append({
            "order_no": o.order_no,
            "order_date": o.order_date.isoformat() if o.order_date else None,
            "product_name": o.product_name,
            "sku": o.sku,
            "qty": qty,
            "is_custom": bool(o.is_custom),
            "revenue": round(revenue, 2) if revenue else None,
            "factory_predicted": predicted,
            "break_even_factory": break_even,
            "break_even_buffer": buffer,
            "estimated": estimated,
            "note": note,
        })
        if predicted is not None:
            tot_predicted += predicted
        if revenue:
            tot_revenue += revenue
        if break_even is not None:
            tot_break_even += break_even
        if buffer is not None:
            tot_buffer += buffer
        if predicted is None or break_even is None:
            missing += 1

    return {
        "period": period,
        "count": len(rows),
        "missing": missing,
        "totals": {
            "revenue": round(tot_revenue, 2),
            "factory_predicted": round(tot_predicted, 2),
            "break_even_factory": round(tot_break_even, 2),
            "break_even_buffer": round(tot_buffer, 2),
        },
        "rows": rows,
    }
