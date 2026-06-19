"""剩余流水（可用资金）测算 + 投资回收 — 业务需求自定义公式。

可用资金 = Σ加项 − Σ减项  (总投资费用不在内 — 它是沉没本金, 单列做投资回收对比)

加项 (现在还有多少能动用的钱):
  (a) 平台保证金            — 手动常量 (settings: cashflow_shop_deposit)
  (b) 支付宝账户余额(全部)  — AccountBalance 最新期末, 账户名含「支付宝」全部账户
  (c) 淘宝聚合结算账户余额  — 账户名含「聚合」
  (d) 推广账户余额          — 账户名含「推广」
  (e) 其他账户余额(银行卡等)— 其余账户 (银行卡/个体户私账等也是真金, 计入)
  (f) 订单待确认收货金额    — Σ paid_amount, status=shipped (已发货未签收)
  (g) 订单未发货金额        — Σ paid_amount, status=paid   (已付款未发货)

减项 (即将要付出去的钱):
  (a) 待扣平台服务费        — (待确认收货 + 未发货) × 千分之六 (卖家服务费列常空, 故估算)
  (b) 工厂打样费(未付)      — Σ factory_bill_amount, 未付 + 无平台单号 (待账单)
  (c) 工厂结算(已开账单未付)— Σ factory_bill_amount, 未付 + 有平台单号
  (d) 工厂结算(未开账单预估)— 活跃单(待发货+待确认)的预测工厂成本; 定制单按基础成本
  (e) 代付补单佣金(未结)    — 补单记录 commission, 无支付宝流水号(未付)

投资回收 (单列, 不进可用资金):
  总投资费用 (手动) ↔ 累计总利润 (compute_total_profit) → 回收率 / 是否回本。
  「总投资」的对应面是「总利润」而非「可用资金」, 二者不能混在一个减法里。

设计要点:
  - 不做「手动结算」，每次实时计算；订单/流水/余额一更新即反映。
  - 同时返回各数据源的「截止时间 + 新鲜度红绿灯」(账户余额日期问题见 AccountBalance.as_of_date)。
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance import AccountBalance, RefillRecord
from app.models.order import FactoryOrder, Order
from app.models.shop_deposit import ShopDeposit
from app.services import factory_payment_service, order_cost_service, settings_service

# ── 手动常量配置键 ────────────────────────────────────────────
SETTING_SHOP_DEPOSIT = "cashflow_shop_deposit"
SETTING_TOTAL_INVESTMENT = "cashflow_total_investment"
DEFAULT_TOTAL_INVESTMENT = Decimal("669871")
DEFAULT_SHOP_DEPOSIT = Decimal("0")

# 平台服务费率 ~千分之六 (软件服务费); 淘宝补贴税 2%
_PLATFORM_FEE_RATE = Decimal("0.006")
_TAX_RATE = Decimal("0.02")

# 新鲜度阈值（天）
FRESH_DAYS = 7
AGING_DAYS = 31

_Q = Decimal("0.01")


def _d(v) -> Decimal:
    if v is None:
        return Decimal("0")
    return Decimal(str(v))


def _get_setting_decimal(db: Session, key: str, default: Decimal) -> Decimal:
    raw = settings_service.get(db, key, env_fallback=False)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return Decimal(str(raw))
    except Exception:
        return default


# ── 账户余额：取每个账户最新一期的期末余额，按账户名归类 ──────────
def _latest_balances(db: Session) -> list[AccountBalance]:
    rows = db.execute(select(AccountBalance)).scalars().all()
    latest: dict[str, AccountBalance] = {}
    for r in rows:
        cur = latest.get(r.account_name)
        if cur is None or (r.period_year, r.period_month) > (cur.period_year, cur.period_month):
            latest[r.account_name] = r
    return list(latest.values())


def _classify_account(name: str) -> str:
    n = name or ""
    if "支付宝" in n:
        return "alipay"
    if "聚合" in n:
        return "aggregate"
    if "推广" in n:
        return "promotion"
    return "other"


# ── 新鲜度 ────────────────────────────────────────────────────
def _freshness(label: str, as_of: Optional[datetime]) -> dict:
    """返回 {source, as_of, days_ago, status}. status: fresh / aging / stale / unknown."""
    if as_of is None:
        return {"source": label, "as_of": None, "days_ago": None, "status": "unknown"}
    now = datetime.now(timezone.utc)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    days = (now - as_of).days
    if days <= FRESH_DAYS:
        status = "fresh"
    elif days <= AGING_DAYS:
        status = "aging"
    else:
        status = "stale"
    return {"source": label, "as_of": as_of.isoformat(), "days_ago": days, "status": status}


def _as_datetime(d) -> Optional[datetime]:
    """date / datetime → tz-aware datetime (供新鲜度按业务日期比较); None 透传。"""
    if d is None:
        return None
    if isinstance(d, datetime):
        return d
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def _sum_paid(db: Session, status: str) -> Decimal:
    """某状态(非历史)订单的买家实付合计 — 现金流"待确认收货/未发货"在途金额。"""
    return _d(db.execute(
        select(func.coalesce(func.sum(Order.paid_amount), 0)).where(
            Order.status == status, Order.is_historical == False,  # noqa: E712
            Order.is_refill == False,  # 刷单是假单, 不算在途真实资金 (2026-06-19)
        )
    ).scalar())


def _factory_unpaid_bill(db: Session, *, has_platform_no: bool) -> Decimal:
    """未付工厂账单(已开票): factory_bill_amount 非空、未付、未作废, 按有无平台单号区分打样/结算。"""
    cond = (FactoryOrder.platform_order_no.isnot(None) if has_platform_no
            else FactoryOrder.platform_order_no.is_(None))
    return _d(db.execute(
        select(func.coalesce(func.sum(FactoryOrder.factory_bill_amount), 0)).where(
            FactoryOrder.payment_status == "unpaid",
            FactoryOrder.voided_at.is_(None),
            FactoryOrder.factory_bill_amount.isnot(None),
            cond,
        )
    ).scalar())


def _predicted_factory_cost(db: Session, order: Order) -> Optional[Decimal]:
    """单订单工厂制造成本预估(订单总额): theoretical_cost(已是订单总额) > 现算BOM total > 定价表 factory_cost×qty。

    注(2026-06-20): theoretical_cost 改为存「订单总额」(单件×真实计价件数), 故此处不再 ×qty
    (原 ×qty 对 qty 脏单 —— 如餐边柜 qty=16 —— 会把成本虚高 16 倍)。"""
    qty = Decimal(int(order.qty or 1))
    if order.theoretical_cost is not None:
        return _d(order.theoretical_cost)
    try:
        bd = order_cost_service.compute(db, order)
        if bd.resolved and not bd.cost_incomplete and bd.total_cost:
            return _d(bd.total_cost)
    except Exception:  # pragma: no cover - 成本反推失败不拖垮现金流
        pass
    from app.models.pricing import PricingSku
    from app.services import sku_utils
    if order.sku_code:
        base = sku_utils.strip_custom_suffix(order.sku_code)
        fc = db.execute(
            select(PricingSku.factory_cost).where(PricingSku.sku_code == base)
        ).scalar_one_or_none()
        if fc is not None:
            return _d(fc) * qty
    return None


def _factory_estimate_unbilled(db: Session) -> tuple[Decimal, int, int]:
    """未开账单工厂结算预估: 活跃单(待发货+待确认收货)预测工厂制造成本合计。
    已有工厂账单的订单跳过(已计入"已开账单未付", 防双算)。
    定制单按基础成本估(缺定制需求的单见异常台账)。返回 (合计, 计入单数, 缺成本单数)。"""
    billed_order_nos = {
        r for (r,) in db.execute(
            select(FactoryOrder.platform_order_no).where(
                FactoryOrder.factory_bill_amount.isnot(None),
                FactoryOrder.platform_order_no.isnot(None),
            )
        ).all()
    }
    orders = db.execute(
        select(Order).where(
            Order.status.in_(("paid", "shipped")),
            Order.is_refill == False,  # noqa: E712
            Order.is_historical == False,  # noqa: E712
        )
    ).scalars().all()
    total = Decimal("0")
    counted = missing = 0
    for o in orders:
        if o.order_no in billed_order_nos:
            continue  # 已开账单 → 不重复预估
        c = _predicted_factory_cost(db, o)
        if c is None:
            missing += 1
        else:
            total += c
            counted += 1
    return total.quantize(_Q), counted, missing


def _refill_unpaid_commission(db: Session) -> Decimal:
    """代付补单佣金(未结): 补单记录 commission 合计, 取尚无支付宝流水号(未付)的。"""
    return _d(db.execute(
        select(func.coalesce(func.sum(RefillRecord.commission), 0)).where(
            RefillRecord.alipay_flow_no.is_(None),
        )
    ).scalar())


def compute_total_profit(db: Session) -> dict:
    """累计总利润 — 与总投资对比看回本。统一会计口径 (与 accounting_summary/逐单核对/资产公式B 一致):

    成交口径 = settled_sale_clause (已付款·非待付款/取消/关闭·非全额退款) + 排补单;
    单单净利 = (实付−退款) − 会计总成本(物理+物流+安装+平台扣点+税+额外售后)。
    旧版营收用店铺实收、成本不含平台/税、售后单列, 与月度报表对不上, 故 2026-06-18 统一。
    """
    from app.services import order_financials as ofin
    from app.services.sales_analytics import settled_sale_clause
    coef = ofin.load_coefficients(db)
    as_by_order = ofin.extra_aftersales_by_order(db)
    orders = db.execute(
        select(Order).where(
            Order.is_refill == False,  # noqa: E712
            settled_sale_clause(),
        )
    ).scalars().all()
    revenue = cost = Decimal("0")
    missing_cost = 0
    for o in orders:
        revenue += _d(o.paid_amount) - _d(o.refund_amount)
        if o.actual_cost is None and o.theoretical_cost is None:
            missing_cost += 1
        cost += ofin.accounting_cost(o, coef, aftersales=_d(as_by_order.get(o.order_no, 0)))
    net = (revenue - cost).quantize(_Q)
    return {
        "order_count": len(orders),
        "revenue": revenue.quantize(_Q),
        "cost": cost.quantize(_Q),
        "expense": Decimal("0.00"),   # 物流/安装/平台/税/售后已并入会计总成本, 不再单列
        "net_profit": net,
        "orders_missing_cost": missing_cost,
    }


def compute_summary(db: Session) -> dict:
    """实时测算剩余流水(可用资金) + 投资回收(总利润vs总投资) + 数据新鲜度。

    可用资金 = Σ加项 − Σ减项 (总投资费用不在内 — 它是沉没本金, 单列做投资回收对比)。
    """
    # ── 加项 ─────────────────────────────────────────────
    # 平台保证金: 优先用多店铺条目(ShopDeposit)求和; 无条目时回退旧单常量(向后兼容)
    deposit_total, deposit_count = _shop_deposit_entries(db)
    if deposit_count > 0:
        shop_deposit = deposit_total
        deposit_source = f"保证金条目({deposit_count} 店铺)"
    else:
        shop_deposit = _get_setting_decimal(db, SETTING_SHOP_DEPOSIT, DEFAULT_SHOP_DEPOSIT)
        deposit_source = "手动维护(单值)"

    bal_alipay = bal_aggregate = bal_promotion = bal_other = Decimal("0")
    balances = _latest_balances(db)
    latest_period: Optional[tuple[int, int]] = None
    bal_as_of: Optional[date] = None   # 余额统计日期 (新鲜度按此算, 不用入库时间)
    for b in balances:
        cat = _classify_account(b.account_name)
        amt = _d(b.closing_balance)
        if cat == "alipay":
            bal_alipay += amt
        elif cat == "aggregate":
            bal_aggregate += amt
        elif cat == "promotion":
            bal_promotion += amt
        else:
            bal_other += amt
        period = (b.period_year, b.period_month)
        if latest_period is None or period > latest_period:
            latest_period = period
        if b.as_of_date and (bal_as_of is None or b.as_of_date > bal_as_of):
            bal_as_of = b.as_of_date

    awaiting_receipt = _sum_paid(db, "shipped")   # 待确认收货 (已发货未签收)
    not_shipped = _sum_paid(db, "paid")           # 未发货 (已付款未发货)
    order_active = awaiting_receipt + not_shipped

    # ── 减项 ─────────────────────────────────────────────
    # 平台服务费: 卖家服务费列常为空, 直接按在途订单金额 ×千分之六 估
    platform_fee = (order_active * _PLATFORM_FEE_RATE).quantize(_Q)
    factory_sample = _factory_unpaid_bill(db, has_platform_no=False)   # 工厂打样(待账单)
    factory_billed = _factory_unpaid_bill(db, has_platform_no=True)    # 工厂结算-已开账单未付
    factory_estimate, fe_counted, fe_missing = _factory_estimate_unbilled(db)  # 未开账单预估
    refill_commission = _refill_unpaid_commission(db)

    additions = [
        {"key": "shop_deposit", "label": "平台保证金", "amount": shop_deposit, "manual": True, "source": deposit_source},
        {"key": "alipay_balance", "label": "支付宝账户余额(全部)", "amount": bal_alipay, "manual": False, "source": "账户余额汇总"},
        {"key": "aggregate_balance", "label": "淘宝聚合结算余额", "amount": bal_aggregate, "manual": False, "source": "账户余额汇总"},
        {"key": "promotion_balance", "label": "推广账户余额", "amount": bal_promotion, "manual": False, "source": "账户余额汇总"},
        {"key": "other_balance", "label": "其他账户余额(银行卡等)", "amount": bal_other, "manual": False, "source": "账户余额汇总"},
        {"key": "awaiting_receipt", "label": "订单待确认收货金额", "amount": awaiting_receipt, "manual": False, "source": "订单(已发货)"},
        {"key": "not_shipped", "label": "订单未发货金额", "amount": not_shipped, "manual": False, "source": "订单(已付款)"},
    ]
    subtractions = [
        {"key": "platform_fee", "label": "待扣平台服务费(在途×0.6%)", "amount": platform_fee, "manual": False, "source": "在途订单估算"},
        {"key": "factory_sample", "label": "工厂打样费(未付·待账单)", "amount": factory_sample, "manual": False, "source": "工厂订单(未付·无平台单号)"},
        {"key": "factory_billed", "label": "工厂结算(已开账单未付)", "amount": factory_billed, "manual": False, "source": "工厂订单(未付·有平台单号)"},
        {"key": "factory_estimate", "label": "工厂结算(未开账单·预测成本)", "amount": factory_estimate, "manual": False,
         "source": f"活跃单预测成本({fe_counted}单" + (f", {fe_missing}单缺成本未计)" if fe_missing else ")")},
        {"key": "refill_commission", "label": "代付补单佣金(未结)", "amount": refill_commission, "manual": False, "source": "补单记录"},
    ]

    total_add = sum((a["amount"] for a in additions), Decimal("0"))
    total_sub = sum((s["amount"] for s in subtractions), Decimal("0"))
    total = (total_add - total_sub).quantize(_Q)

    # ── 投资回收 (总投资 vs 累计总利润; 不进可用资金) ──────────
    total_investment = _get_setting_decimal(db, SETTING_TOTAL_INVESTMENT, DEFAULT_TOTAL_INVESTMENT)
    profit = compute_total_profit(db)
    net_profit = Decimal(str(profit["net_profit"]))
    recovery_rate = float(net_profit / total_investment) if total_investment else None
    investment = {
        "total_investment": total_investment,
        "total_profit": net_profit,
        "recovered": (net_profit >= total_investment) if total_investment else None,
        "recovery_rate": round(recovery_rate, 4) if recovery_rate is not None else None,
        "remaining": (total_investment - net_profit).quantize(_Q),
        "profit_detail": profit,
    }

    # ── 新鲜度: 用业务日期(数据截止)而非入库时间 ──────────────
    # 订单 → 最后下单日; 账户余额 → 统计日期(as_of_date); 都不再用 updated_at(=导入那天=今天)。
    orders_latest = db.execute(select(func.max(Order.order_date))).scalar()
    factory_latest = db.execute(select(func.max(FactoryOrder.order_date))).scalar()
    deposit_row_at = _setting_updated_at(db, SETTING_SHOP_DEPOSIT)
    investment_at = _setting_updated_at(db, SETTING_TOTAL_INVESTMENT)

    bal_label = "账户余额"
    if bal_as_of:
        bal_label = f"账户余额（统计日 {bal_as_of.isoformat()}）"
    elif latest_period:
        bal_label = f"账户余额（{latest_period[0]}-{latest_period[1]:02d}·缺统计日期）"

    freshness = [
        _freshness("订单数据(最后下单日)", _as_datetime(orders_latest)),
        _freshness(bal_label, _as_datetime(bal_as_of)),
        _freshness("工厂订单(最后下单日)", _as_datetime(factory_latest)),
        _freshness("总投资费用", investment_at),
    ]
    if deposit_row_at is not None:
        freshness.append(_freshness("店铺保证金", deposit_row_at))

    return {
        "total": total,
        "total_additions": total_add.quantize(_Q),
        "total_subtractions": total_sub.quantize(_Q),
        "additions": additions,
        "subtractions": subtractions,
        "investment": investment,
        "other_account_balance": Decimal("0"),  # 银行卡等已并入加项, 不再单列
        "freshness": freshness,
        "manual": {
            "shop_deposit": shop_deposit,
            "total_investment": total_investment,
            "factory_settlement_days": factory_payment_service.get_settlement_days(db),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _shop_deposit_entries(db: Session) -> tuple[Decimal, int]:
    """多店铺保证金条目: 返回 (合计, 条目数)。无条目时 (0, 0) → 调用方回退旧单常量。"""
    total, count = db.execute(
        select(func.coalesce(func.sum(ShopDeposit.amount), 0), func.count(ShopDeposit.id))
    ).one()
    return (Decimal(total or 0), int(count or 0))


def _setting_updated_at(db: Session, key: str) -> Optional[datetime]:
    from app.models.settings import SystemSetting
    row = db.execute(select(SystemSetting).where(SystemSetting.key == key)).scalar_one_or_none()
    return row.updated_at if row else None


def update_manual(
    db: Session,
    *,
    shop_deposit: Optional[Decimal] = None,
    total_investment: Optional[Decimal] = None,
    factory_settlement_days: Optional[int] = None,
) -> None:
    """更新手动常量（店铺保证金 / 总投资费用 / 工厂结算周期）。"""
    if shop_deposit is not None:
        settings_service.set_value(
            db, SETTING_SHOP_DEPOSIT, str(shop_deposit), description="剩余流水-店铺保证金",
        )
    if total_investment is not None:
        settings_service.set_value(
            db, SETTING_TOTAL_INVESTMENT, str(total_investment), description="剩余流水-总投资费用",
        )
    if factory_settlement_days is not None:
        factory_payment_service.set_settlement_days(db, factory_settlement_days)
