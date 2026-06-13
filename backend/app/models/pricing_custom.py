"""定价自定义字段 (EAV) — 用户在定价表里自建任意列(数值/文本)、可改名, 按 SKU 填值。

PricingCustomField: 列定义(标签可改 / 类型 / 排序)。
PricingCustomValue: 某 SKU 在某自定义列上的值 (数值或文本)。
与既有 pricing_sku / costs / promo 完全隔离, 仅用于定价表展示与编辑, 不参与公式重算。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from sqlalchemy import ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class PricingCustomField(Base, TimestampMixin):
    __tablename__ = "pricing_custom_fields"
    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(64), nullable=False)          # 列名(可改)
    value_kind: Mapped[str] = mapped_column(String(8), default="number")    # number | text
    sort_order: Mapped[int] = mapped_column(default=0)


class PricingCustomValue(Base, TimestampMixin):
    __tablename__ = "pricing_custom_values"
    __table_args__ = (UniqueConstraint("sku_code", "field_id", name="uq_pcv_sku_field"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    sku_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    field_id: Mapped[int] = mapped_column(
        ForeignKey("pricing_custom_fields.id", ondelete="CASCADE"), nullable=False, index=True
    )
    num_value: Mapped[Optional[Decimal]] = mapped_column(Numeric(16, 4))
    text_value: Mapped[Optional[str]] = mapped_column(Text)
