"""Append-only Taobao SKU identity ledger and physical-slot proposals."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class SkuIdentity(Base, TimestampMixin):
    """Canonical meaning of one observed Taobao item/SKU identity.

    Meaning fields are immutable.  A later observation with a different meaning
    is recorded as a conflict and never overwrites this row.
    """

    __tablename__ = "sku_identities"

    id: Mapped[int] = mapped_column(primary_key=True)
    taobao_item_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    taobao_sku_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    merchant_code: Mapped[Optional[str]] = mapped_column(String(96), index=True)
    sku_spec: Mapped[Optional[str]] = mapped_column(Text)
    sku_code: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    product_code: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    is_custom_placeholder: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    identity_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    first_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    latest_sale_state: Mapped[Optional[str]] = mapped_column(String(32))
    latest_daily_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    latest_evidence_source: Mapped[str] = mapped_column(String(128), nullable=False)
    latest_evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    conflict_detected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint("taobao_item_id", "taobao_sku_id", name="uq_sku_identity_item_sku"),
    )


class SkuIdentityObservation(Base):
    """Immutable evidence event. Rows are inserted only, never updated."""

    __tablename__ = "sku_identity_observations"

    id: Mapped[int] = mapped_column(primary_key=True)
    identity_id: Mapped[Optional[int]] = mapped_column(ForeignKey("sku_identities.id"), index=True)
    taobao_item_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    taobao_sku_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    merchant_code: Mapped[Optional[str]] = mapped_column(String(96))
    sku_spec: Mapped[Optional[str]] = mapped_column(Text)
    sku_code: Mapped[Optional[str]] = mapped_column(String(64))
    product_code: Mapped[Optional[str]] = mapped_column(String(64))
    is_custom_placeholder: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    daily_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    sale_state: Mapped[Optional[str]] = mapped_column(String(32))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    evidence_source: Mapped[str] = mapped_column(String(128), nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    identity_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    disposition: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[Optional[dict]] = mapped_column(JSON)


class SkuPhysicalSlotProposal(Base, TimestampMixin):
    """Auditable not-yet-live slot proposal; it never masquerades as Taobao state."""

    __tablename__ = "sku_physical_slot_proposals"

    id: Mapped[int] = mapped_column(primary_key=True)
    taobao_item_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    parent_taobao_sku_id: Mapped[Optional[str]] = mapped_column(String(64))
    parent_merchant_code: Mapped[str] = mapped_column(String(96), nullable=False)
    target_merchant_code: Mapped[str] = mapped_column(String(96), nullable=False, unique=True)
    source_option: Mapped[str] = mapped_column(String(255), nullable=False)
    target_option: Mapped[str] = mapped_column(String(255), nullable=False)
    slot_number: Mapped[int] = mapped_column(nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    authorization_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    product_create_status: Mapped[str] = mapped_column(String(32), nullable=False)
    product_save_status: Mapped[str] = mapped_column(String(32), nullable=False)
    campaign_signup_status: Mapped[str] = mapped_column(String(32), nullable=False)
    proposed_fields: Mapped[Optional[dict]] = mapped_column(JSON)
    evidence_source: Mapped[str] = mapped_column(String(128), nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint("taobao_item_id", "slot_number", name="uq_sku_physical_slot_item_number"),
    )
