"""财务相关模型 (Excel 表 8/9/10/11 → plan §3, §8)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Date, DateTime, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

ALIPAY_ACCOUNTS = ("企业号", "个体户私账", "爱群号", "佳宝号", "主力号")


class AlipayFlow(Base, TimestampMixin):
    """支付宝流水 (9a~9e 五张表合并存)。

    通过 account 字段区分。流水号在同一账户内全局唯一。
    """

    __tablename__ = "alipay_flows"

    id: Mapped[int] = mapped_column(primary_key=True)
    account: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    transaction_no: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    transaction_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    transaction_type: Mapped[Optional[str]] = mapped_column(String(64))  # 分账 / 在线支付 / 转账 / ...
    counterparty: Mapped[Optional[str]] = mapped_column(String(255))
    counterparty_account: Mapped[Optional[str]] = mapped_column(String(255))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)  # 正=收入, 负=支出
    related_order_no: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    balance: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    reconciliation_status: Mapped[str] = mapped_column(String(16), default="open", nullable=False, index=True)
    # open / matched / mismatched / ignored / opening_balance (期初调整)
    reconciliation_type: Mapped[Optional[str]] = mapped_column(String(32))
    # factory_payment / promotion / refill_compensation / logistics / install / opening / other
    remark: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("account", "transaction_no", name="uq_alipay_flow_acct_no"),
        Index("ix_alipay_flows_acct_time", "account", "transaction_time"),
    )


class AccountBalance(Base, TimestampMixin):
    """账户余额汇总 (Excel 表 10)，按账户按月一行。

    plan §9: 期初调整 = 一次性把 Excel 当前余额作为开账日（2025-12-31）的期初值；
    后续每月跑 AccountBalanceService 重算。
    """

    __tablename__ = "account_balances"

    id: Mapped[int] = mapped_column(primary_key=True)
    sync_key: Mapped[Optional[str]] = mapped_column(String(128), unique=True, nullable=True)
    account_name: Mapped[str] = mapped_column(String(64), nullable=False)
    account_no: Mapped[Optional[str]] = mapped_column(String(128))
    period_year: Mapped[int] = mapped_column(Integer, nullable=False)
    period_month: Mapped[int] = mapped_column(Integer, nullable=False)
    opening_balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"), nullable=False)
    income: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"), nullable=False)
    expense: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"), nullable=False)
    closing_balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"), nullable=False)
    remark: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("account_name", "period_year", "period_month", name="uq_account_balance_period"),
    )


class RefillRecord(Base, TimestampMixin):
    """补单记录 (Excel 表 8)。"""

    __tablename__ = "refill_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    buyer_nick: Mapped[Optional[str]] = mapped_column(String(128))
    refill_date: Mapped[Optional[date]] = mapped_column(Date, index=True)
    product_code: Mapped[Optional[str]] = mapped_column(String(32))
    product_name: Mapped[Optional[str]] = mapped_column(String(255))
    sku: Mapped[Optional[str]] = mapped_column(String(255))
    order_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    qty: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    refill_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    refill_freight: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    platform_fee: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    # 业务需求 §5: 补单只算佣金 + 快递; 平台/税务回到 Order.profit 计算
    commission: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    total_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))


class FactoryReconciliation(Base, TimestampMixin):
    """工厂对账汇总 (Excel 表 11)。"""

    __tablename__ = "factory_reconciliations"

    id: Mapped[int] = mapped_column(primary_key=True)
    sync_key: Mapped[Optional[str]] = mapped_column(String(128), unique=True, nullable=True)
    period_start: Mapped[Optional[date]] = mapped_column(Date)
    period_end: Mapped[Optional[date]] = mapped_column(Date)
    factory_name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    order_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))   # 本期下单金额
    bill_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))    # 工厂账单金额
    paid_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))    # 实际支付
    reconciled_at: Mapped[Optional[date]] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)
    diff_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    diff_reason: Mapped[Optional[str]] = mapped_column(Text)
    alipay_flow_no: Mapped[Optional[str]] = mapped_column(String(64))
    remark: Mapped[Optional[str]] = mapped_column(Text)
