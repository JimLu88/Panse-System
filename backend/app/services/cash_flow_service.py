"""剩余流水（可用资金）测算 — 业务需求自定义公式。

剩余流水 = Σ加项 − Σ减项

加项:
  (a) 店铺保证金            — 手动常量 (settings: cashflow_shop_deposit)
  (b) 支付宝账户余额        — AccountBalance 最新期末 (账户名含「支付宝」)
  (c) 淘宝聚合结算账户余额  — AccountBalance 最新期末 (账户名含「聚合」)
  (d) 推广账户余额          — AccountBalance 最新期末 (账户名含「推广」)
  (e) 订单待确认收货金额    — Σ paid_amount, status=shipped (已发货未签收)
  (f) 订单未发货金额        — Σ paid_amount, status=paid   (已付款未发货)

减项:
  (a) 待扣除平台服务费      — Σ platform_fee, status∈{paid, shipped}
  (b) 工厂打样费(未付)      — Σ factory_bill_amount, 未付 + 无平台订单号 (打样件)
  (c) 工厂订单结算费(未付)  — Σ factory_bill_amount, 未付 + 有平台订单号 (正式单)
  (d) 待付款刷单金额        — Σ paid_amount, is_refill=True 且 status=pending_payment
  (e) 总投资费用            — 手动常量 (settings: cashflow_total_investment)

设计要点:
  - 不做「手动结算」，每次实时计算；订单/流水/余额一更新即反映。
  - 同时返回各数据源的「截止时间 + 新鲜度红绿灯」，
    让大屏在显示数字的同时诚实标出哪块数据可能过期。
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance import AccountBalance
from app.models.order import FactoryOrder, Order
from app.services import settings_service

# ── 手动常量配置键 ────────────────────────────────────────────
SETTING_SHOP_DEPOSIT = "cashflow_shop_deposit"
SETTING_TOTAL_INVESTMENT = "cashflow_total_investment"
DEFAULT_TOTAL_INVESTMENT = Decimal("669871")
DEFAULT_SHOP_DEPOSIT = Decimal("0")

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


def compute_summary(db: Session) -> dict:
    """实时计算剩余流水及其明细 + 数据新鲜度。"""
    # ── 加项 ─────────────────────────────────────────────
    shop_deposit = _get_setting_decimal(db, SETTING_SHOP_DEPOSIT, DEFAULT_SHOP_DEPOSIT)

    bal_alipay = Decimal("0")
    bal_aggregate = Decimal("0")
    bal_promotion = Decimal("0")
    bal_other = Decimal("0")
    balances = _latest_balances(db)
    latest_period: Optional[tuple[int, int]] = None
    bal_updated_at: Optional[datetime] = None
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
        if b.updated_at and (bal_updated_at is None or b.updated_at > bal_updated_at):
            bal_updated_at = b.updated_at

    # 订单在途金额
    awaiting_receipt = _d(
        db.execute(
            select(func.coalesce(func.sum(Order.paid_amount), 0)).where(
                Order.status == "shipped", Order.is_historical == False,  # noqa: E712
            )
        ).scalar()
    )
    not_shipped = _d(
        db.execute(
            select(func.coalesce(func.sum(Order.paid_amount), 0)).where(
                Order.status == "paid", Order.is_historical == False,  # noqa: E712
            )
        ).scalar()
    )

    # ── 减项 ─────────────────────────────────────────────
    pending_platform_fee = _d(
        db.execute(
            select(func.coalesce(func.sum(Order.platform_fee), 0)).where(
                Order.status.in_(("paid", "shipped")), Order.is_historical == False,  # noqa: E712
            )
        ).scalar()
    )

    # 工厂未付：有平台订单号→正式订单结算费；无→打样费 (尽力拆分，合计稳健)
    factory_settlement = _d(
        db.execute(
            select(func.coalesce(func.sum(FactoryOrder.factory_bill_amount), 0)).where(
                FactoryOrder.payment_status == "unpaid",
                FactoryOrder.voided_at.is_(None),
                FactoryOrder.platform_order_no.isnot(None),
            )
        ).scalar()
    )
    factory_sample = _d(
        db.execute(
            select(func.coalesce(func.sum(FactoryOrder.factory_bill_amount), 0)).where(
                FactoryOrder.payment_status == "unpaid",
                FactoryOrder.voided_at.is_(None),
                FactoryOrder.platform_order_no.is_(None),
            )
        ).scalar()
    )

    pending_brush = _d(
        db.execute(
            select(func.coalesce(func.sum(Order.paid_amount), 0)).where(
                Order.is_refill == True,  # noqa: E712
                Order.status == "pending_payment",
                Order.is_historical == False,  # noqa: E712
            )
        ).scalar()
    )

    total_investment = _get_setting_decimal(db, SETTING_TOTAL_INVESTMENT, DEFAULT_TOTAL_INVESTMENT)

    # ── 汇总 ─────────────────────────────────────────────
    additions = [
        {"key": "shop_deposit", "label": "店铺保证金", "amount": shop_deposit, "manual": True, "source": "手动维护"},
        {"key": "alipay_balance", "label": "支付宝账户余额", "amount": bal_alipay, "manual": False, "source": "账户余额汇总"},
        {"key": "aggregate_balance", "label": "淘宝聚合结算余额", "amount": bal_aggregate, "manual": False, "source": "账户余额汇总"},
        {"key": "promotion_balance", "label": "推广账户余额", "amount": bal_promotion, "manual": False, "source": "账户余额汇总"},
        {"key": "awaiting_receipt", "label": "订单待确认收货金额", "amount": awaiting_receipt, "manual": False, "source": "订单(已发货)"},
        {"key": "not_shipped", "label": "订单未发货金额", "amount": not_shipped, "manual": False, "source": "订单(已付款)"},
    ]
    subtractions = [
        {"key": "pending_platform_fee", "label": "待扣除平台服务费", "amount": pending_platform_fee, "manual": False, "source": "订单(未结算)"},
        {"key": "factory_sample", "label": "工厂打样费(未付)", "amount": factory_sample, "manual": False, "source": "工厂订单(未付·无平台单号)"},
        {"key": "factory_settlement", "label": "工厂订单结算费(未付)", "amount": factory_settlement, "manual": False, "source": "工厂订单(未付·有平台单号)"},
        {"key": "pending_brush", "label": "待付款刷单金额", "amount": pending_brush, "manual": False, "source": "订单(补单·待付款)"},
        {"key": "total_investment", "label": "总投资费用", "amount": total_investment, "manual": True, "source": "手动维护"},
    ]

    total_add = sum((a["amount"] for a in additions), Decimal("0"))
    total_sub = sum((s["amount"] for s in subtractions), Decimal("0"))
    total = (total_add - total_sub).quantize(_Q)

    # ── 新鲜度 ───────────────────────────────────────────
    orders_updated = db.execute(select(func.max(Order.updated_at))).scalar()
    factory_updated = db.execute(select(func.max(FactoryOrder.updated_at))).scalar()
    deposit_row_at = _setting_updated_at(db, SETTING_SHOP_DEPOSIT)
    investment_at = _setting_updated_at(db, SETTING_TOTAL_INVESTMENT)

    bal_label = "账户余额"
    if latest_period:
        bal_label = f"账户余额（{latest_period[0]}-{latest_period[1]:02d}）"

    freshness = [
        _freshness("订单数据", orders_updated),
        _freshness(bal_label, bal_updated_at),
        _freshness("工厂订单", factory_updated),
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
        "other_account_balance": bal_other,  # 其他账户（银行卡/万师傅等），未计入公式，仅供参考
        "freshness": freshness,
        "manual": {
            "shop_deposit": shop_deposit,
            "total_investment": total_investment,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _setting_updated_at(db: Session, key: str) -> Optional[datetime]:
    from app.models.settings import SystemSetting
    row = db.execute(select(SystemSetting).where(SystemSetting.key == key)).scalar_one_or_none()
    return row.updated_at if row else None


def update_manual(
    db: Session,
    *,
    shop_deposit: Optional[Decimal] = None,
    total_investment: Optional[Decimal] = None,
) -> None:
    """更新手动常量（店铺保证金 / 总投资费用）。"""
    if shop_deposit is not None:
        settings_service.set_value(
            db, SETTING_SHOP_DEPOSIT, str(shop_deposit), description="剩余流水-店铺保证金",
        )
    if total_investment is not None:
        settings_service.set_value(
            db, SETTING_TOTAL_INVESTMENT, str(total_investment), description="剩余流水-总投资费用",
        )
