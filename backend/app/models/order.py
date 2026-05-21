"""订单总表 / 工厂下单 / 配件采购 (Excel 表 5/6/7 → plan §3)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, Date, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

# 订单状态机（plan §3 表 5）
ORDER_STATUS = ("pending_payment", "paid", "shipped", "signed", "aftersales", "cancelled")

# 合法状态迁移图：from -> {to, ...}
ORDER_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "pending_payment": {"paid", "cancelled"},
    "paid": {"shipped", "aftersales", "cancelled"},
    "shipped": {"signed", "aftersales"},
    "signed": {"aftersales"},
    "aftersales": {"signed"},  # 售后结束回签收
    "cancelled": set(),
}


class Order(Base, TimestampMixin):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # 淘宝 / 抖音 / 直营
    order_no: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    is_refill: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # 是否补单

    order_date: Mapped[Optional[date]] = mapped_column(Date, index=True)
    ship_date: Mapped[Optional[date]] = mapped_column(Date)

    customer_name: Mapped[Optional[str]] = mapped_column(String(64))
    customer_phone: Mapped[Optional[str]] = mapped_column(String(32))
    customer_address: Mapped[Optional[str]] = mapped_column(String(255))

    product_code: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    product_name: Mapped[Optional[str]] = mapped_column(String(255))
    sku: Mapped[Optional[str]] = mapped_column(String(255))
    sku_code: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    qty: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending_payment", nullable=False, index=True)

    carrier: Mapped[Optional[str]] = mapped_column(String(64))
    tracking_no: Mapped[Optional[str]] = mapped_column(String(128))
    install_ticket_no: Mapped[Optional[str]] = mapped_column(String(64))

    # 成本/费用
    theoretical_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    actual_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    actual_freight: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    upstairs_fee: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    install_fee: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    compensation_fee: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    paid_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))

    remark: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (
        Index("ix_orders_platform_date", "platform", "order_date"),
    )


class FactoryOrder(Base, TimestampMixin):
    __tablename__ = "factory_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    factory_order_no: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    platform_order_no: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    factory_name: Mapped[Optional[str]] = mapped_column(String(128))
    order_date: Mapped[Optional[date]] = mapped_column(Date)
    expected_delivery: Mapped[Optional[date]] = mapped_column(Date)
    actual_delivery: Mapped[Optional[date]] = mapped_column(Date)
    product_code: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    sku: Mapped[Optional[str]] = mapped_column(String(255))
    qty: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    unit_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    factory_bill_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    expected_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    payment_method: Mapped[Optional[str]] = mapped_column(String(32))  # 月结 / 现付 / 预付
    payment_status: Mapped[str] = mapped_column(String(32), default="unpaid", nullable=False)
    payment_date: Mapped[Optional[date]] = mapped_column(Date)
    alipay_flow_no: Mapped[Optional[str]] = mapped_column(String(64))
    carrier: Mapped[Optional[str]] = mapped_column(String(64))
    tracking_no: Mapped[Optional[str]] = mapped_column(String(128))
    remark: Mapped[Optional[str]] = mapped_column(Text)


class PartPurchase(Base, TimestampMixin):
    __tablename__ = "part_purchases"

    id: Mapped[int] = mapped_column(primary_key=True)
    purchase_no: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    supplier: Mapped[Optional[str]] = mapped_column(String(128))
    purchase_date: Mapped[Optional[date]] = mapped_column(Date, index=True)
    material_code: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    material_name: Mapped[Optional[str]] = mapped_column(String(255))
    spec: Mapped[Optional[str]] = mapped_column(String(255))
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("1"))
    unit_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    tracking_no: Mapped[Optional[str]] = mapped_column(String(128))
    freight: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    total_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    purchase_type: Mapped[Optional[str]] = mapped_column(String(32))  # 备货 / 单单采 / ...
    related_order_no: Mapped[Optional[str]] = mapped_column(String(64))
    payment_method: Mapped[Optional[str]] = mapped_column(String(32))
    payment_status: Mapped[str] = mapped_column(String(32), default="unpaid", nullable=False)
    payment_date: Mapped[Optional[date]] = mapped_column(Date)
    alipay_flow_no: Mapped[Optional[str]] = mapped_column(String(64))
    remark: Mapped[Optional[str]] = mapped_column(Text)
