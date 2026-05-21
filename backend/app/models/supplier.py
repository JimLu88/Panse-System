"""供应商对账模块 (业务需求扩展).

通用供应商: 木作工厂 / 岩板厂 / 玻璃厂 + 未来自定义。
每个供应商有多张送货单 (DeliveryNote), 每张含多行 (DeliveryNoteLine)。
原图按 supplier/year/month 归档于 DeliveryFile。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


SUPPLIER_TYPES = ("woodwork", "rock_slab", "glass", "hardware", "logistics", "other")


class Supplier(Base, TimestampMixin):
    __tablename__ = "suppliers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    supplier_type: Mapped[str] = mapped_column(String(32), default="other", nullable=False)
    contact: Mapped[Optional[str]] = mapped_column(String(128))
    phone: Mapped[Optional[str]] = mapped_column(String(32))
    address: Mapped[Optional[str]] = mapped_column(String(255))
    payment_terms: Mapped[Optional[str]] = mapped_column(String(64))  # 月结 / 现付 / 预付
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    remark: Mapped[Optional[str]] = mapped_column(Text)

    # 支付宝自动对账 (业务需求 2): 关键字命中 counterparty 时视为这家供应商的付款
    alipay_counterparty_keywords: Mapped[Optional[list]] = mapped_column(JSON, default=list)
    alipay_account: Mapped[Optional[str]] = mapped_column(String(32))  # 主要从哪个支付宝账号付


class DeliveryNote(Base, TimestampMixin):
    """供应商送货单一张 = 一次到货 / 一次结账单据."""
    __tablename__ = "delivery_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), nullable=False, index=True)
    note_no: Mapped[Optional[str]] = mapped_column(String(64), index=True)  # 单号
    delivery_date: Mapped[Optional[date]] = mapped_column(Date, index=True)
    total_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))

    # OCR 来源信息
    source_file_id: Mapped[Optional[int]] = mapped_column(ForeignKey("delivery_files.id"))
    ocr_model: Mapped[Optional[str]] = mapped_column(String(64))
    ocr_warnings: Mapped[Optional[list]] = mapped_column(JSON, default=list)
    ocr_confidence: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))  # 0-100

    # 对账流转
    status: Mapped[str] = mapped_column(String(16), default="pending_review", nullable=False, index=True)
    # pending_review / confirmed / billed / paid / disputed
    reconciled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    alipay_flow_no: Mapped[Optional[str]] = mapped_column(String(64))

    remark: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (
        Index("ix_delivery_notes_supplier_date", "supplier_id", "delivery_date"),
        UniqueConstraint("supplier_id", "note_no", name="uq_delivery_notes_supplier_note_no"),
    )


class DeliveryNoteLine(Base, TimestampMixin):
    """送货单明细行 — 单条货物."""
    __tablename__ = "delivery_note_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    delivery_note_id: Mapped[int] = mapped_column(
        ForeignKey("delivery_notes.id"), nullable=False, index=True
    )
    line_no: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    item_name: Mapped[Optional[str]] = mapped_column(String(255))
    spec: Mapped[Optional[str]] = mapped_column(String(128))  # 1800×850 etc.
    unit: Mapped[Optional[str]] = mapped_column(String(16))
    qty: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("1"), nullable=False)
    unit_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))

    # 订单匹配 (业务需求: 模糊 + AI 兜底)
    matched_order_no: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    match_confidence: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))  # 0-100
    match_method: Mapped[Optional[str]] = mapped_column(String(32))  # fuzzy / ai / manual / none
    match_candidates: Mapped[Optional[list]] = mapped_column(JSON, default=list)
    # [{"order_no": "...", "confidence": 87.5, "reason": "..."}, ...]

    # OCR 痕迹
    ocr_raw_text: Mapped[Optional[str]] = mapped_column(Text)
    ocr_warnings: Mapped[Optional[list]] = mapped_column(JSON, default=list)

    remark: Mapped[Optional[str]] = mapped_column(Text)


class DeliveryFile(Base, TimestampMixin):
    """上传的送货单原图 — 按 supplier/year/month 归档."""
    __tablename__ = "delivery_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(ForeignKey("suppliers.id"), nullable=False, index=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    original_name: Mapped[Optional[str]] = mapped_column(String(255))
    mime_type: Mapped[Optional[str]] = mapped_column(String(64))
    size_bytes: Mapped[Optional[int]] = mapped_column(Integer)
    uploaded_by: Mapped[Optional[str]] = mapped_column(String(64))

    __table_args__ = (
        Index("ix_delivery_files_supplier_period", "supplier_id", "year", "month"),
    )
