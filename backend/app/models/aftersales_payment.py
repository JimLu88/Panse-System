"""个人支付宝售后打款关联账。

流水是原始事实，本表只保存“这笔钱属于哪个订单/售后”的可审计关系。
一笔流水可拆分多个 allocation_key，但确认金额合计不得超过原流水支出。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    CheckConstraint, DateTime, ForeignKey, Index, Integer, JSON, Numeric, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class AfterSalesPaymentLink(Base, TimestampMixin):
    __tablename__ = "after_sales_payment_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    alipay_flow_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("alipay_flows.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    allocation_key: Mapped[str] = mapped_column(String(64), nullable=False, default="full")
    order_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("orders.id", ondelete="SET NULL"), index=True,
    )
    after_sales_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("after_sales.id", ondelete="SET NULL"), index=True,
    )
    wanshifu_order_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("wanshifu_orders.id", ondelete="SET NULL"), index=True,
    )

    category: Mapped[str] = mapped_column(String(40), nullable=False)
    allocated_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="proposed", index=True)
    match_method: Mapped[Optional[str]] = mapped_column(String(40))
    confidence: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4))
    extracted_order_no: Mapped[Optional[str]] = mapped_column(String(64))
    extracted_customer_name: Mapped[Optional[str]] = mapped_column(String(64))
    evidence_json: Mapped[Optional[dict]] = mapped_column(JSON)
    decision_note: Mapped[Optional[str]] = mapped_column(Text)
    created_by: Mapped[Optional[str]] = mapped_column(String(64))
    decided_by: Mapped[Optional[str]] = mapped_column(String(64))
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        UniqueConstraint(
            "alipay_flow_id", "allocation_key", name="uq_after_sales_payment_flow_allocation",
        ),
        CheckConstraint("allocated_amount > 0", name="ck_after_sales_payment_amount_positive"),
        CheckConstraint(
            "status in ('proposed','confirmed','rejected','voided')",
            name="ck_after_sales_payment_status",
        ),
        CheckConstraint(
            "category in ('price_difference','review_refund','customer_compensation',"
            "'repair_service','onsite_service','return_service','misc_after_sales')",
            name="ck_after_sales_payment_category",
        ),
        CheckConstraint("version > 0", name="ck_after_sales_payment_version_positive"),
        Index("ix_after_sales_payment_status_category", "status", "category"),
    )
