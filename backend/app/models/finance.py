"""财务相关模型 (Excel 表 8/9/10/11 → plan §3, §8)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
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
    transaction_no: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
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

    # 飞书同步配对键: account+流水号+交易类型+金额 拼成的稳定键。
    # 自增 id 两端对不上, 单用 transaction_no 又会把同号配对流水(在线支付+分账)
    # 压成一行, 故按业务唯一键拼 sync_key, 由事件钩子在插入/更新时自动生成。
    sync_key: Mapped[Optional[str]] = mapped_column(String(255), index=True)

    # 导入批次追踪 (C2)
    import_job_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("import_jobs.id", ondelete="SET NULL"), nullable=True, index=True)

    __table_args__ = (
        # 同号配对流水 (在线支付货款 + 分账手续费) 共用同一交易流水号, 必须都能入库;
        # 仅「同号 + 同类型 + 同金额」才算真重复。详见 migration 0039。
        UniqueConstraint(
            "account", "transaction_no", "transaction_type", "amount",
            name="uq_alipay_flow_acct_no",
        ),
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
    remark: Mapped[Optional[str]] = mapped_column(String(255))   # 备注 / 补单状态
    # 飞书同步配对键 (自增 id 两端对不上, 用业务字段拼一个稳定键), 由事件钩子自动生成
    sync_key: Mapped[Optional[str]] = mapped_column(String(160), index=True)


class WanshifuBill(Base, TimestampMixin):
    """万师傅安装账单 — 按月从万师傅后台导出 CSV 导入。

    对账逻辑: Σ amount (按月) vs AlipayFlow[reconciliation_type='install'] (按月)。
    fallback (账单未导入): Σ AfterSales.wanshifu_deduction vs AlipayFlow[install]。
    """
    __tablename__ = "wanshifu_bills"

    id: Mapped[int] = mapped_column(primary_key=True)
    bill_date: Mapped[Optional[date]] = mapped_column(Date, index=True)
    order_no: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    service_type: Mapped[Optional[str]] = mapped_column(String(64))  # 安装 / 维修 / 其他
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[Optional[str]] = mapped_column(String(32))  # 已结算 / 待结算
    remark: Mapped[Optional[str]] = mapped_column(Text)
    import_job_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("import_jobs.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    # 飞书同步配对键, 由事件钩子自动生成
    sync_key: Mapped[Optional[str]] = mapped_column(String(160), index=True)


class LogisticsBill(Base, TimestampMixin):
    """物流费账单 — 按月从物流公司导出月结账单 CSV 导入。

    对账逻辑: Σ freight_amount (按承运商/月) vs AlipayFlow[reconciliation_type='logistics']。
    fallback (账单未导入): Σ Order.actual_freight (按月) vs AlipayFlow[logistics]。
    """
    __tablename__ = "logistics_bills"

    id: Mapped[int] = mapped_column(primary_key=True)
    bill_date: Mapped[Optional[date]] = mapped_column(Date, index=True)
    carrier: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    tracking_no: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    order_no: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    weight_kg: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 3))
    freight_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    remark: Mapped[Optional[str]] = mapped_column(Text)
    import_job_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("import_jobs.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    # 飞书同步配对键, 由事件钩子自动生成
    sync_key: Mapped[Optional[str]] = mapped_column(String(160), index=True)


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

    # 导入批次追踪 (C2)
    import_job_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("import_jobs.id", ondelete="SET NULL"), nullable=True, index=True)


# ---------------------------------------------------------------------------
# 飞书同步配对键自动生成
# 这三张账单表用自增 id 做主键, 两端 id 对不上, 不能作为飞书同步的配对键。
# 这里按业务字段拼一个稳定的 sync_key, 通过 SQLAlchemy 事件在插入/更新时自动填充,
# 覆盖所有创建入口 (Excel 导入 / 扫描 / 异常修复 等), 无需逐个改调用点。
# ---------------------------------------------------------------------------
from sqlalchemy import event  # noqa: E402


def _part(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (date, datetime)):
        return v.isoformat()[:10]
    return str(v).strip()


def _refill_sync_key(o: "RefillRecord") -> str:
    return "refill:" + ":".join(_part(x) for x in (o.order_no, o.refill_date, o.sku, o.qty))


def _wanshifu_sync_key(o: "WanshifuBill") -> str:
    return "wsf:" + ":".join(_part(x) for x in (o.order_no, o.bill_date, o.service_type, o.amount))


def _logistics_sync_key(o: "LogisticsBill") -> str:
    if o.tracking_no:
        return "log:" + _part(o.tracking_no)
    return "log:" + ":".join(_part(x) for x in (o.order_no, o.bill_date, o.carrier, o.freight_amount))


def _alipay_sync_key(o: "AlipayFlow") -> str:
    # 与唯一约束 (account, transaction_no, transaction_type, amount) 一致:
    # 同号配对流水(在线支付+分账)交易类型/金额不同, 键不同, 两端都能配对; 完全相同才算同一行。
    return "alipay:" + ":".join(
        _part(x) for x in (o.account, o.transaction_no, o.transaction_type, o.amount)
    )


def _register_sync_key(model, fn):
    @event.listens_for(model, "before_insert")
    @event.listens_for(model, "before_update")
    def _set_sync_key(_mapper, _conn, target):  # noqa: ANN001
        target.sync_key = fn(target)


_register_sync_key(RefillRecord, _refill_sync_key)
_register_sync_key(WanshifuBill, _wanshifu_sync_key)
_register_sync_key(LogisticsBill, _logistics_sync_key)
_register_sync_key(AlipayFlow, _alipay_sync_key)
