"""客户主表 (Phase 9, Tier 2 #5, 借鉴 Shopify CRM).

业务: 从订单聚合出唯一客户 (按 phone + 标准化 address 匹配),
计算 LTV / 复购率 / 客户分级. 客服面对客户时能看历史.

字段:
    matching_key      = phone + name 标准化, 用于去重
    tier              = bronze/silver/gold/platinum (按 LTV 自动分)
    first_order_at / last_order_at
    total_orders / total_revenue / total_returns
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import JSON, DateTime, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Customer(Base, TimestampMixin):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    address: Mapped[Optional[str]] = mapped_column(String(512))

    matching_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True, unique=True)
    # phone + name 简单合一; 同一人多 ID 时人工合并

    tier: Mapped[str] = mapped_column(String(16), default="bronze", nullable=False)
    # bronze / silver / gold / platinum

    first_order_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_order_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    total_orders: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_revenue: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"), nullable=False)
    total_returns: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    tags: Mapped[Optional[list]] = mapped_column(JSON, default=list)
    # VIP / 客单价高 / 投诉过 等业务标签 (List[str])

    note: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (
        Index("ix_customers_tier", "tier"),
    )