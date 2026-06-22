"""逐笔对账 (per-order reconciliation) — 用户要求: 不按月总额对账, 而是逐笔订单核对。

每条订单一行, 横向铺开「四方对账」:
  收入侧:
    1. 买家应付货款 (payable)   — 卖家优惠后买家应付
    2. 买家实付金额 (paid)      — 买家实际支付; (应付-实付)=平台优惠券补贴 subsidy
    3. 店铺实收金额 (received)  — 平台口径净收 ≈ 应付 - 补贴税(2%) - 软件服务费
    4. 实际到账 (arrived)       — 真金白银: 微信/聚合(order_settlements) + 支付宝(alipay_flows)
  成本侧:
    5. 理论成本 (theoretical_cost) / 实际成本(工厂) (actual_cost)

关键税费规则 (用户口径):
  - 淘宝平台优惠券补贴属于商家应税收入, 约 2% 税费经支付宝另付, 系统没单独记。
    实测订单总表: tax == 买家应付 × 2% (805/805 精确), 且 店铺实收 = 应付 - 税 - 软件服务费。
    故对账时 理论应到账 = 应付 - 2%补贴税 - 软件服务费, 与 实收 一致 (±1元 取整)。

到账证据稀疏时 (流水尚未导全 / 早期订单未入库 / 企业号订单号与订单表不同号段),
诚实标 status='pending' (待补流水), 不强行判差异。
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.finance import AlipayFlow
from app.models.order import Order
from app.models.settlement import OrderSettlement
from app.services import recon_config_service

_CENT = Decimal("0.01")
_TAX_RATE = Decimal("0.02")  # 淘宝补贴 2% 税费 (商家应税收入)


def _d(x) -> Optional[Decimal]:
    return Decimal(str(x)) if x is not None else None


def _f(x) -> Optional[float]:
    return float(x) if x is not None else None


def _wechat_net_by_order(db: Session) -> dict[str, Decimal]:
    """微信/聚合 (order_settlements) 按订单号净到账 = Σ(收入 - 支出)。"""
    out: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    rows = db.execute(
        select(OrderSettlement.order_no, OrderSettlement.income, OrderSettlement.expense)
        .where(OrderSettlement.order_no.isnot(None))
    ).all()
    for ono, inc, exp in rows:
        out[str(ono).strip()] += (inc or Decimal("0")) - (exp or Decimal("0"))
    return dict(out)


def _alipay_net_by_flow_no(db: Session) -> dict[str, Decimal]:
    """支付宝按交易流水号汇总金额 (同号货款+分账成对, 取净额)。

    排除订单级分账 (related_order_no 以 T200P 开头): 这些已由 settlement_import_service.route_alipay_flows
    路由进 order_settlements, 走微信口径按订单号汇总; 若这里再按 flow_no 算一遍会双算到账 (用户拍板 2026-06-23)。
    """
    out: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for no, amt in db.execute(
        select(AlipayFlow.transaction_no, AlipayFlow.amount).where(
            AlipayFlow.transaction_no.isnot(None),
            or_(AlipayFlow.related_order_no.is_(None),
                AlipayFlow.related_order_no.notlike("T200P%")),
        )
    ).all():
        out[str(no).strip()] += (amt or Decimal("0"))
    return dict(out)


def _coupon_clawback_by_order(db: Session) -> dict[str, Decimal]:
    """每单消费券代付扣回金额 (order_settlements 里 description 含「消费券」的支出之和)。

    用于把「消费券待补」单标成低优状态 (status='coupon_pending'): 平台先垫消费券进货款又扣回,
    要约 2 月后才分批补回 (出资合作), 这期间该单到账差额是已知的、不该当真·有差异红着催 (用户拍板 2026-06-23)。
    """
    out: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for ono, exp in db.execute(
        select(OrderSettlement.order_no, OrderSettlement.expense).where(
            OrderSettlement.order_no.isnot(None),
            OrderSettlement.description.like("%消费券%"),
        )
    ).all():
        out[str(ono).strip()] += (exp or Decimal("0"))
    return dict(out)


def _alipay_settled_orders(db: Session) -> set[str]:
    """order_settlements 里 source='alipay' 的订单号 — 这些到账来自支付宝企业号分账路由,
    渠道应标「支付宝」而非「微信」(到账证据都进 order_settlements, 单靠表无法分渠道, 用 source 区分)。"""
    return {
        str(o).strip()
        for (o,) in db.execute(
            select(OrderSettlement.order_no).where(
                OrderSettlement.order_no.isnot(None),
                OrderSettlement.source == "alipay",
            )
        ).all()
    }


def _tax_of(o: Order, cfg: dict) -> Optional[Decimal]:
    """补贴税: 优先用订单存的 tax; 缺则按 买家应付 × 补贴税率(按店铺可配) 估算。"""
    if o.tax is not None:
        return _d(o.tax)
    if o.buyer_payable_amount is not None:
        tax_rate = recon_config_service.rate(cfg, "subsidy_tax_rate", o.shop)
        return (_d(o.buyer_payable_amount) * tax_rate).quantize(_CENT)
    return None


def _expected_net(o: Order, tax: Optional[Decimal], fee: Optional[Decimal]) -> Optional[Decimal]:
    """理论应到账: 优先店铺实收; 缺则 应付 - 2%税 - 软件费; 再缺退买家实付。"""
    if o.shop_received_amount is not None:
        return _d(o.shop_received_amount)
    if o.buyer_payable_amount is not None:
        return _d(o.buyer_payable_amount) - (tax or Decimal("0")) - (fee or Decimal("0"))
    return _d(o.paid_amount)


def _tolerance(base: Optional[Decimal], cfg: dict) -> Decimal:
    """到账差额容差: 取 容差最小金额 与 容差百分比 的较大者 (可在设置里调)。"""
    floor = recon_config_service.rate(cfg, "tolerance_floor")
    pct = recon_config_service.rate(cfg, "tolerance_pct")
    if base is None:
        return floor
    return max(floor, (abs(base) * pct).quantize(_CENT))


def _build_row(o: Order, wnet: dict, anet: dict, cfg: dict, coupon: Optional[dict] = None,
               alipay_orders: Optional[set] = None) -> dict:
    payable = _d(o.buyer_payable_amount)
    paid = _d(o.paid_amount)
    received = _d(o.shop_received_amount)
    tax = _tax_of(o, cfg)
    fee = _d(o.platform_fee)
    if fee is None and payable is not None:  # 缺软件费时按费率(按店铺可配)估算
        fee = (payable * recon_config_service.rate(cfg, "software_fee_rate", o.shop)).quantize(_CENT)
    subsidy = (payable - paid) if (payable is not None and paid is not None and payable > paid) else None

    wechat = wnet.get(o.order_no)
    alipay = anet.get(o.alipay_flow_no) if o.alipay_flow_no else None
    channels = []
    if wechat is not None:
        # order_settlements 来源可能是 微信/聚合 或 支付宝企业号(路由进来的) — 按 source 标对渠道
        channels.append("支付宝" if (alipay_orders and o.order_no in alipay_orders) else "微信")
    if alipay is not None and "支付宝" not in channels:
        channels.append("支付宝")
    has_evidence = bool(channels)
    arrived = (wechat or Decimal("0")) + (alipay or Decimal("0")) if has_evidence else None

    expected = _expected_net(o, tax, fee)
    diff = (arrived - expected) if (arrived is not None and expected is not None) else None
    coupon_clawback = (coupon or {}).get(o.order_no, Decimal("0"))
    if not has_evidence:
        status = "pending"
    elif diff is not None and abs(diff) <= _tolerance(expected, cfg):
        status = "matched"
    elif coupon_clawback > 0:
        # 该单有消费券代付扣回未补回 → 到账差额是已知的「消费券待补」(约2月分批补回), 低优不催, 不算真差异
        status = "coupon_pending"
    else:
        status = "diff"

    theo = _d(o.theoretical_cost)
    actual = _d(o.actual_cost)
    cost_diff = (actual - theo) if (actual is not None and theo is not None) else None

    return {
        "order_no": o.order_no,
        "order_date": o.order_date.isoformat() if o.order_date else None,
        "shop": o.shop,
        "product_name": o.product_name,
        "customer_name": o.customer_name,
        "is_custom": o.is_custom,
        # 收入侧
        "payable": _f(payable),
        "paid": _f(paid),
        "subsidy": _f(subsidy),
        "received": _f(received),
        "tax": _f(tax),
        "platform_fee": _f(fee),
        "expected_net": _f(expected),
        "arrived": _f(arrived),
        "wechat_net": _f(wechat),
        "alipay_net": _f(alipay),
        "channels": channels,
        "diff": _f(diff),
        "status": status,
        "coupon_clawback": _f(coupon_clawback) if coupon_clawback > 0 else None,  # 消费券代付扣回(约2月补回)
        # 成本侧 (四方对账 3/4)
        "theoretical_cost": _f(theo),
        "actual_cost": _f(actual),
        "cost_diff": _f(cost_diff),
        "refund_amount": _f(_d(o.refund_amount)),
    }


def _base_query():
    """纳入逐笔对账的订单: 真实销售 (非补单/非关闭) 且至少有一个金额信号。"""
    return select(Order).where(
        Order.is_refill == False,  # noqa: E712
        Order.status.is_distinct_from("cancelled"),  # 关闭单=正常退款无货品交易, 不进对账(¥183万假付款脏值, 用户铁律); NULL-safe 保留未知状态
        (Order.buyer_payable_amount.isnot(None))
        | (Order.paid_amount.isnot(None))
        | (Order.shop_received_amount.isnot(None)),
    )


def per_order(
    db: Session, *,
    limit: int = 200, offset: int = 0,
    status: Optional[str] = None, channel: Optional[str] = None, q: Optional[str] = None,
) -> dict:
    """逐笔对账列表 (分页 + 状态/渠道/关键词筛选)。"""
    wnet = _wechat_net_by_order(db)
    anet = _alipay_net_by_flow_no(db)
    coupon = _coupon_clawback_by_order(db)
    alipay_orders = _alipay_settled_orders(db)
    cfg = recon_config_service.get_config(db)

    stmt = _base_query()
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where((Order.order_no.ilike(like)) | (Order.customer_name.ilike(like)))
    stmt = stmt.order_by(Order.order_date.desc().nulls_last(), Order.id.desc())
    orders = db.execute(stmt).scalars().all()

    rows = [_build_row(o, wnet, anet, cfg, coupon, alipay_orders) for o in orders]
    if status:
        rows = [r for r in rows if r["status"] == status]
    if channel == "wechat":
        rows = [r for r in rows if "微信" in r["channels"]]
    elif channel == "alipay":
        rows = [r for r in rows if "支付宝" in r["channels"]]
    elif channel == "none":
        rows = [r for r in rows if not r["channels"]]

    total = len(rows)
    page = rows[offset:offset + limit]
    return {"total": total, "rows": page}


def coverage_gap(db: Session) -> dict:
    """到账覆盖缺口诊断: 按月统计有无到账证据, 指出最该补流水/账单的几个月。

    覆盖率低 = 早期订单未导 / billDetail / 企业号流水没补全。按月铺开让用户知道补哪批。
    """
    wnet = _wechat_net_by_order(db)
    anet = _alipay_net_by_flow_no(db)
    alipay_settled = _alipay_settled_orders(db)
    cfg = recon_config_service.get_config(db)
    orders = db.execute(_base_query()).scalars().all()

    by_month: dict[str, dict] = {}
    tot_orders = tot_evidence = 0
    tot_pending_amt = Decimal("0")
    for o in orders:
        r = _build_row(o, wnet, anet, cfg, None, alipay_settled)
        mk = o.order_date.strftime("%Y-%m") if o.order_date else "无日期"
        b = by_month.setdefault(mk, {
            "period": mk, "orders": 0, "evidence": 0, "pending": 0,
            "pending_amount": Decimal("0"), "wechat": 0, "alipay": 0,
        })
        b["orders"] += 1
        tot_orders += 1
        if r["channels"]:
            b["evidence"] += 1
            tot_evidence += 1
            b["wechat"] += "微信" in r["channels"]
            b["alipay"] += "支付宝" in r["channels"]
        else:
            b["pending"] += 1
            if r["expected_net"]:
                amt = Decimal(str(r["expected_net"]))
                b["pending_amount"] += amt
                tot_pending_amt += amt

    months = []
    for mk in sorted(by_month, reverse=True):
        b = by_month[mk]
        b["coverage_pct"] = round(b["evidence"] / b["orders"] * 100, 1) if b["orders"] else 0.0
        b["pending_amount"] = float(b["pending_amount"])
        months.append(b)
    worst = [m["period"] for m in
             sorted(months, key=lambda m: m["pending_amount"], reverse=True)[:5]
             if m["pending"] > 0]
    return {
        "total_orders": tot_orders,
        "evidence_orders": tot_evidence,
        "pending_orders": tot_orders - tot_evidence,
        "coverage_pct": round(tot_evidence / tot_orders * 100, 1) if tot_orders else 0.0,
        "pending_amount": float(tot_pending_amt),
        "months": months,
        "worst_months": worst,    # 缺口最大(待补流水金额最高)的几个月
    }


def coverage_gap_detail(db: Session, period: str) -> dict:
    """Plan L1: 某月待补订单清单 + 行动指引 — 缺微信 billDetail 还是缺支付宝流水。"""
    wnet = _wechat_net_by_order(db)
    anet = _alipay_net_by_flow_no(db)
    cfg = recon_config_service.get_config(db)
    orders = db.execute(_base_query()).scalars().all()
    rows = []
    for o in orders:
        mk = o.order_date.strftime("%Y-%m") if o.order_date else "无日期"
        if mk != period:
            continue
        r = _build_row(o, wnet, anet, cfg)
        if r["status"] != "pending":
            continue
        missing = []
        if wnet.get(o.order_no) is None:
            missing.append("微信billDetail")
        if not o.alipay_flow_no or anet.get(o.alipay_flow_no) is None:
            missing.append("支付宝流水")
        rows.append({
            "order_no": o.order_no,
            "order_date": o.order_date.isoformat() if o.order_date else None,
            "shop": o.shop, "customer_name": o.customer_name,
            "product_name": o.product_name,
            "expected_net": r.get("expected_net"),
            "missing": missing,
        })
    n_wx = sum(1 for r in rows if "微信billDetail" in r["missing"])
    n_zfb = sum(1 for r in rows if "支付宝流水" in r["missing"])
    actions = []
    if n_wx:
        actions.append(f"{n_wx} 单缺微信结算证据 → 到「结算/导入」上传该月 billDetail CSV")
    if n_zfb:
        actions.append(f"{n_zfb} 单缺支付宝流水绑定 → 导入企业号流水后跑「支付宝↔订单 自动匹配」")
    if period < "2026-01":
        actions.append("早期订单可能本就没有线上凭据 → 核对后可在逐笔对账里标 ignored 做平")
    return {"period": period, "pending_count": len(rows),
            "rows": rows[:500], "actions": actions}


def summary(db: Session) -> dict:
    """全量汇总 (不分页): 各金额合计 + 对账状态分布 + 到账覆盖率。"""
    wnet = _wechat_net_by_order(db)
    anet = _alipay_net_by_flow_no(db)
    coupon = _coupon_clawback_by_order(db)
    alipay_settled = _alipay_settled_orders(db)
    cfg = recon_config_service.get_config(db)
    orders = db.execute(_base_query()).scalars().all()

    agg = {k: Decimal("0") for k in
           ("payable", "paid", "received", "tax", "platform_fee", "subsidy", "arrived")}
    matched = diff = pending = coupon_pending = evidence = wechat_orders = alipay_orders = 0
    for o in orders:
        r = _build_row(o, wnet, anet, cfg, coupon, alipay_settled)
        for k in agg:
            v = r.get(k)
            if v is not None:
                agg[k] += Decimal(str(v))
        status = r["status"]
        matched += status == "matched"
        diff += status == "diff"
        pending += status == "pending"
        coupon_pending += status == "coupon_pending"
        if r["channels"]:
            evidence += 1
        if "微信" in r["channels"]:
            wechat_orders += 1
        if "支付宝" in r["channels"]:
            alipay_orders += 1

    n = len(orders)
    return {
        "orders": n,
        "payable_sum": float(agg["payable"]),
        "paid_sum": float(agg["paid"]),
        "received_sum": float(agg["received"]),
        "tax_sum": float(agg["tax"]),               # 2% 补贴税合计
        "platform_fee_sum": float(agg["platform_fee"]),
        "subsidy_sum": float(agg["subsidy"]),        # 平台优惠券补贴合计
        "arrived_sum": float(agg["arrived"]),
        "matched": matched,
        "diff": diff,
        "pending": pending,
        "coupon_pending": coupon_pending,   # 消费券待补(低优, 约2月补回; 不计入有差异)
        "evidence_orders": evidence,
        "wechat_orders": wechat_orders,
        "alipay_orders": alipay_orders,
        "coverage_pct": round(evidence / n * 100, 1) if n else 0.0,
        "tax_rate": float(_TAX_RATE),
    }
