"""营销与售后相关模型 (Excel 表 12/13/14/15/17/18 → plan §3)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import Date, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Sample(Base, TimestampMixin):
    """13-样品表."""
    __tablename__ = "samples"

    id: Mapped[int] = mapped_column(primary_key=True)
    sample_no: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    product_code: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    product_name: Mapped[Optional[str]] = mapped_column(String(255))
    sku: Mapped[Optional[str]] = mapped_column(String(255))
    sample_type: Mapped[Optional[str]] = mapped_column(String(32))  # 测试样品 / 摄影样品 / 展示样品
    qty: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    made_at: Mapped[Optional[date]] = mapped_column(Date)
    cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    location: Mapped[Optional[str]] = mapped_column(String(128))
    status: Mapped[Optional[str]] = mapped_column(String(32))  # 在用 / 闲置 / 报损
    usage: Mapped[Optional[str]] = mapped_column(String(128))
    remark: Mapped[Optional[str]] = mapped_column(Text)


class BrandMarketing(Base, TimestampMixin):
    """14-品牌营销."""
    __tablename__ = "brand_marketing"

    id: Mapped[int] = mapped_column(primary_key=True)
    sync_key: Mapped[Optional[str]] = mapped_column(String(128), unique=True, nullable=True)
    project_name: Mapped[str] = mapped_column(String(255), nullable=False)
    project_type: Mapped[Optional[str]] = mapped_column(String(64))
    partner: Mapped[Optional[str]] = mapped_column(String(255))
    start_date: Mapped[Optional[date]] = mapped_column(Date)
    end_date: Mapped[Optional[date]] = mapped_column(Date)
    budget: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    actual_spend: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    payment_date: Mapped[Optional[date]] = mapped_column(Date)
    status: Mapped[Optional[str]] = mapped_column(String(32))
    effect_eval: Mapped[Optional[str]] = mapped_column(Text)
    alipay_flow_no: Mapped[Optional[str]] = mapped_column(String(64))
    remark: Mapped[Optional[str]] = mapped_column(Text)


class PromotionFlow(Base, TimestampMixin):
    """15-推广记录 (淘宝推广账户的充值/消耗流水)."""
    __tablename__ = "promotion_flows"

    id: Mapped[int] = mapped_column(primary_key=True)
    sync_key: Mapped[Optional[str]] = mapped_column(String(128), unique=True, nullable=True)
    transaction_date: Mapped[Optional[date]] = mapped_column(Date, index=True)
    flow_type: Mapped[Optional[str]] = mapped_column(String(32))  # 充值 / 支出 / 退款
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    alipay_flow_no: Mapped[Optional[str]] = mapped_column(String(64))
    remark: Mapped[Optional[str]] = mapped_column(Text)


class OutsourcingExpense(Base, TimestampMixin):
    """17-人员外包费用."""
    __tablename__ = "outsourcing_expenses"

    id: Mapped[int] = mapped_column(primary_key=True)
    sync_key: Mapped[Optional[str]] = mapped_column(String(128), unique=True, nullable=True)
    alipay_flow_no: Mapped[Optional[str]] = mapped_column(String(64))
    payee: Mapped[str] = mapped_column(String(128), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    project: Mapped[Optional[str]] = mapped_column(String(128))
    related_order_no: Mapped[Optional[str]] = mapped_column(String(64))
    cost_category: Mapped[Optional[str]] = mapped_column(String(32))  # 固定成本 / 变动成本
    payment_date: Mapped[Optional[date]] = mapped_column(Date, index=True)
    remark: Mapped[Optional[str]] = mapped_column(Text)


class AfterSales(Base, TimestampMixin):
    """18-售后表."""
    __tablename__ = "after_sales"

    id: Mapped[int] = mapped_column(primary_key=True)
    platform_order_no: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    reason: Mapped[Optional[str]] = mapped_column(String(255))
    compensation_fee: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))  # 订单赔付费
    good_review_refund: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))  # 好评/差价返
    in_platform_total: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))  # 平台内售后总成本
    direct_compensation: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))  # 直接赔付客户
    second_visit_fee: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))  # 二次上门维修费
    return_pack_freight: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))  # 返厂打包运费
    out_platform_total: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))  # 平台外售后总成本
    refill_sku: Mapped[Optional[str]] = mapped_column(String(255))
    refill_tracking_no: Mapped[Optional[str]] = mapped_column(String(128))
    refill_freight: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    wanshifu_deduction: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))  # 万师傅扣款
    factory_compensation: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    logistics_compensation: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    alipay_flow_no: Mapped[Optional[str]] = mapped_column(String(64))
    second_inbound_confirmed: Mapped[Optional[str]] = mapped_column(String(8))
    processed_at: Mapped[Optional[date]] = mapped_column(Date)
    status: Mapped[Optional[str]] = mapped_column(String(32))
    customer_satisfaction: Mapped[Optional[str]] = mapped_column(String(32))
    remark: Mapped[Optional[str]] = mapped_column(Text)


class WoodLoss(Base, TimestampMixin):
    """12-木材损耗."""
    __tablename__ = "wood_losses"

    id: Mapped[int] = mapped_column(primary_key=True)
    sync_key: Mapped[Optional[str]] = mapped_column(String(128), unique=True, nullable=True)
    purchase_date: Mapped[Optional[date]] = mapped_column(Date)
    wood_type: Mapped[Optional[str]] = mapped_column(String(64))
    spec: Mapped[Optional[str]] = mapped_column(String(128))
    unit: Mapped[Optional[str]] = mapped_column(String(16))
    inbound_qty: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4))
    used_qty: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4))
    loss_qty: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4))
    loss_rate_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4))
    related_product_qty: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4))
    reason: Mapped[Optional[str]] = mapped_column(String(255))
    disposition: Mapped[Optional[str]] = mapped_column(String(255))
    remark: Mapped[Optional[str]] = mapped_column(Text)


class DailyOperation(Base, TimestampMixin):
    """日常经营记录 — 用于飞书同步的经营流水。"""
    __tablename__ = "daily_operations"

    id: Mapped[int] = mapped_column(primary_key=True)
    sync_key: Mapped[Optional[str]] = mapped_column(String(128), unique=True, nullable=True)
    record_date: Mapped[Optional[date]] = mapped_column(Date, index=True)
    category: Mapped[Optional[str]] = mapped_column(String(64))
    item: Mapped[Optional[str]] = mapped_column(String(255))
    amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    qty: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4))
    unit: Mapped[Optional[str]] = mapped_column(String(16))
    payment_account: Mapped[Optional[str]] = mapped_column(String(64))   # 支付账户
    expense_type: Mapped[Optional[str]] = mapped_column(String(64))      # 支出类型
    recipient: Mapped[Optional[str]] = mapped_column(String(128))        # 支付对象
    payment_method: Mapped[Optional[str]] = mapped_column(String(64))    # 支付方式
    alipay_flow_no: Mapped[Optional[str]] = mapped_column(String(64))    # 支付宝流水号
    invoice_status: Mapped[Optional[str]] = mapped_column(String(32))    # 发票状态
    remark: Mapped[Optional[str]] = mapped_column(Text)
