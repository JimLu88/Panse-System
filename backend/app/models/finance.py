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
    # 平台订单号 (表 9 与"关联订单号"分列; 爱群号等账户会把多笔订单号拼在一格 → Text 不限长, 不建索引)
    platform_order_no: Mapped[Optional[str]] = mapped_column(Text)
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
        # 同号多笔真实流水 (成对的货款+分账, 或同号多次扣费) 共用同一交易流水号, 必须都能入库;
        # 把 balance(交易后余额)纳入键 — 每笔交易后余额不同 → 视为不同流水; 五者全同才算真重复。
        # 详见 migration 0039(初版四元组) 与 0057(加 balance)。
        UniqueConstraint(
            "account", "transaction_no", "transaction_type", "amount", "balance",
            name="uq_alipay_flow_acct_no",
        ),
        Index("ix_alipay_flows_acct_time", "account", "transaction_time"),
        # 对账规则全部按 reconciliation_type 过滤 (run_factory_payment/promotion/install/...)
        Index("ix_alipay_flows_recon_type", "reconciliation_type"),
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
    # 统计日期 (这条余额是哪天的快照) — 余额常是某天手填的, 不是"今天";
    # 新鲜度红绿灯按这个算, 而非入库时间 updated_at (迁移 0059)。
    as_of_date: Mapped[Optional[date]] = mapped_column(Date, index=True)
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
    # 表 8-补单记录 字段补全 (Excel 导入)
    supplier_payment: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))  # 供应商打款费用
    alipay_flow_no: Mapped[Optional[str]] = mapped_column(String(64))            # 支付宝流水号
    tracking_no: Mapped[Optional[str]] = mapped_column(String(128))              # 物流单号
    fee_remark: Mapped[Optional[str]] = mapped_column(Text)                      # 费用备注
    remark: Mapped[Optional[str]] = mapped_column(String(255))   # 备注 / 补单状态
    # 飞书同步配对键 (自增 id 两端对不上, 用业务字段拼一个稳定键), 由事件钩子自动生成
    sync_key: Mapped[Optional[str]] = mapped_column(String(160), index=True)


class StaffSalary(Base, TimestampMixin):
    """人员/工资档案 (G: 自由增减人员、改月工资)。

    外包成本口径挂钩: order_financials.outsourcing_for_range 每月预估额改为
    Σ 当月在职人员 monthly_cost (替代写死 ¥10000)。
    在职判定: active_from <= 月末 且 (active_to is None 或 active_to >= 月初)。
    """

    __tablename__ = "staff_salaries"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    monthly_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"), nullable=False)
    role: Mapped[Optional[str]] = mapped_column(String(32))
    active_from: Mapped[date] = mapped_column(Date, nullable=False)
    active_to: Mapped[Optional[date]] = mapped_column(Date)  # None = 至今
    remark: Mapped[Optional[str]] = mapped_column(Text)


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


class WanshifuOrder(Base, TimestampMixin):
    """万师傅安装订单档案 — 后台「订单导出」38 列格式 (2026-06 起默认格式)。

    与 WanshifuBill (月结账单, 对账用) 不同: 这张表是逐单服务档案,
    含客户信息, 用于和淘宝订单配对 → 售后/安装费自动挂单。
    表里没有淘宝订单号, 配对靠 手机号/姓名+城市/物流单号 多层启发式,
    结果写 matched_order_no + match_method; 对不上留空给人工。
    """
    __tablename__ = "wanshifu_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    wsf_order_no: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    service_type: Mapped[Optional[str]] = mapped_column(String(64))     # 家具|安装
    status: Mapped[Optional[str]] = mapped_column(String(64))           # 交易成功 / 交易关闭…
    product_category: Mapped[Optional[str]] = mapped_column(String(64))  # 桌类-餐台/餐桌
    product_model: Mapped[Optional[str]] = mapped_column(String(255))   # 商品型号 (自由文本)
    customer_name: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    customer_phone: Mapped[Optional[str]] = mapped_column(String(32))   # 可能带 -分机 虚拟号
    province: Mapped[Optional[str]] = mapped_column(String(32))
    city: Mapped[Optional[str]] = mapped_column(String(32))
    district: Mapped[Optional[str]] = mapped_column(String(32))
    address: Mapped[Optional[str]] = mapped_column(String(255))
    net_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))   # 订单总净额
    service_fee: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))  # 订单服务费
    created_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    finished_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    tracking_company: Mapped[Optional[str]] = mapped_column(String(64))
    tracking_no: Mapped[Optional[str]] = mapped_column(String(128))
    source_shop: Mapped[Optional[str]] = mapped_column(String(128))
    matched_order_no: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    match_method: Mapped[Optional[str]] = mapped_column(String(32))   # phone_full/name_city/…/multi/none
    match_note: Mapped[Optional[str]] = mapped_column(Text)           # 多候选清单/未匹配原因
    remark: Mapped[Optional[str]] = mapped_column(Text)
    import_job_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("import_jobs.id", ondelete="SET NULL"), nullable=True, index=True,
    )


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
    billing_period: Mapped[Optional[str]] = mapped_column(String(64))   # 对账周期 (如 "2026年1月结账")
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
    # 与唯一约束 (account, transaction_no, transaction_type, amount, balance) 一致:
    # 同号多笔流水(货款+分账 / 同号多次扣费)类型/金额/余额不同, 键不同, 两端都能配对; 五者全同才算同一行。
    return "alipay:" + ":".join(
        _part(x) for x in (o.account, o.transaction_no, o.transaction_type, o.amount, o.balance)
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
