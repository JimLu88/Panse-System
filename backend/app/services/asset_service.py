"""资产汇总 (Phase 4, 业务需求 14/19).

汇总 各账户余额 / 应收 / 库存账面 / 待发货 等, 给前端饼图.

业务需求 19: 公式 A = 初始 + 保证金 + 各类余额 + 待确认收货 + 未发货 - 未支付平台费
            - 未支付工厂打样 - 未支付工厂结算 - 未支付刷单佣金 - 未支付人员费
对比 B = 订单利润 + 账户余额. 差额生成 "未核销异常池" Alert.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.finance import AccountBalance, AlipayFlow
from app.models.inventory import PartInventory, ProductInventory
from app.models.material import Material
from app.models.order import FactoryOrder, Order


@dataclass
class AssetCategory:
    name: str
    amount: Decimal
    detail: list[dict] = field(default_factory=list)


@dataclass
class AssetSummary:
    total: Decimal
    categories: list[AssetCategory] = field(default_factory=list)
    formula_a: Decimal = Decimal("0")
    formula_b: Decimal = Decimal("0")
    diff: Decimal = Decimal("0")


def _account_balances(db: Session) -> Decimal:
    """各账户最新月末 closing_balance 之和."""
    # 找每个账户最大 (year, month) 的 closing_balance
    rows = db.execute(
        select(AccountBalance).order_by(AccountBalance.id.desc())
    ).scalars().all()
    latest_per_account: dict[str, AccountBalance] = {}
    for r in rows:
        if r.account_name not in latest_per_account:
            latest_per_account[r.account_name] = r
    return sum((Decimal(r.closing_balance or 0) for r in latest_per_account.values()),
               Decimal("0"))


def _inventory_book_value(db: Session) -> tuple[Decimal, list[dict]]:
    """配件库存账面价值 = sum(physical_qty × material.price)."""
    rows = db.execute(
        select(PartInventory, Material).join(
            Material, Material.code == PartInventory.material_code,
        )
    ).all()
    total = Decimal("0")
    detail = []
    for inv, mat in rows:
        if mat.price and inv.physical_qty:
            val = (Decimal(mat.price) * Decimal(inv.physical_qty)).quantize(Decimal("0.01"))
            total += val
            detail.append({"material_code": inv.material_code,
                           "qty": inv.physical_qty, "unit_price": float(mat.price),
                           "value": float(val)})
    return total, detail


def _pending_shipment_value(db: Session) -> Decimal:
    """已付未发货订单的待发货资产 (= paid_amount)."""
    rows = db.execute(
        select(func.coalesce(func.sum(Order.paid_amount), 0)).where(
            Order.status == "paid",
            Order.is_historical == False,  # noqa: E712
        )
    ).scalar()
    return Decimal(rows or 0)


def _pending_factory_payment(db: Session) -> Decimal:
    """未支付的工厂订单结算 = sum(factory_bill_amount) where payment_status='unpaid'."""
    rows = db.execute(
        select(func.coalesce(func.sum(FactoryOrder.factory_bill_amount), 0)).where(
            FactoryOrder.payment_status == "unpaid",
            FactoryOrder.voided_at.is_(None),
        )
    ).scalar()
    return Decimal(rows or 0)


def _total_order_profit(db: Session) -> Decimal:
    """所有 status in (paid/shipped/signed) 订单的累计净利润."""
    orders = db.execute(
        select(Order).where(
            Order.is_historical == False,  # noqa: E712
            Order.status.in_(("paid", "shipped", "signed")),
        )
    ).scalars().all()
    total = Decimal("0")
    for o in orders:
        paid = Decimal(o.paid_amount or 0)
        cost = Decimal(o.actual_cost or o.theoretical_cost or 0)
        freight = Decimal(o.actual_freight or 0)
        upstairs = Decimal(o.upstairs_fee or 0)
        install = Decimal(o.install_fee or 0)
        comp = Decimal(o.compensation_fee or 0)
        total += paid - cost - freight - upstairs - install - comp
    return total


def summary(db: Session) -> AssetSummary:
    """业务需求 14 资产总额 + 饼图分类 + 19 公式对比."""
    balances = _account_balances(db)
    inv_value, inv_detail = _inventory_book_value(db)
    pending_ship = _pending_shipment_value(db)
    pending_factory = _pending_factory_payment(db)
    order_profit = _total_order_profit(db)

    # 饼图分类
    categories = [
        AssetCategory(name="账户余额", amount=balances),
        AssetCategory(name="库存账面", amount=inv_value, detail=inv_detail[:50]),
        AssetCategory(name="待发货资产", amount=pending_ship),
    ]
    total = sum((c.amount for c in categories), Decimal("0"))

    # 公式 A: 简化为账户 + 库存 + 待发货 - 待付工厂
    formula_a = balances + inv_value + pending_ship - pending_factory
    # 公式 B: 订单累计利润 + 账户余额
    formula_b = order_profit + balances

    return AssetSummary(
        total=total,
        categories=categories,
        formula_a=formula_a,
        formula_b=formula_b,
        diff=formula_a - formula_b,
    )


# ----------------------------- 未核销异常池 (业务需求 19) -------- #


def unmatched_recent_flows(db: Session, *, days: int = 7) -> list[dict]:
    """业务需求 19: 公式差额可能藏在哪 — 最近 N 天 reconciliation_status='open' 的流水."""
    cutoff = date.today() - timedelta(days=days)
    rows = db.execute(
        select(AlipayFlow).where(
            AlipayFlow.reconciliation_status == "open",
            AlipayFlow.transaction_time >= cutoff,
        ).order_by(AlipayFlow.transaction_time.desc()).limit(200)
    ).scalars().all()
    return [
        {
            "id": r.id,
            "transaction_no": r.transaction_no,
            "transaction_time": r.transaction_time.isoformat() if r.transaction_time else None,
            "amount": float(r.amount),
            "counterparty": r.counterparty,
            "transaction_type": r.transaction_type,
            "remark": r.remark,
        }
        for r in rows
    ]


def check_formula_and_alert(db: Session) -> dict:
    """定时任务调: 算公式差额, > 100 元生成 Alert."""
    from app.services import alert_service
    s = summary(db)
    abs_diff = abs(s.diff)
    if abs_diff > Decimal("100"):
        unmatched = unmatched_recent_flows(db, days=7)
        alert_service.upsert(
            db,
            kind="finance_mismatch",
            severity="warn",
            title=f"账务公式差额 {s.diff:.2f} 元",
            body=(f"A (账面) {s.formula_a:.2f} vs B (订单+余额) {s.formula_b:.2f} = {s.diff:.2f}. "
                  f"最近 7 天 {len(unmatched)} 条未核销流水可能藏着差额."),
            dedupe_key="finance_mismatch:weekly",
            related_url="/finance/reconciliation",
            context={"diff": float(s.diff), "unmatched_count": len(unmatched)},
            auto_resolve_after_minutes=60 * 24,  # 一天后过期 (下次再算一遍)
        )
    return {"diff": float(s.diff), "abs_diff": float(abs_diff),
            "alerted": abs_diff > Decimal("100")}
