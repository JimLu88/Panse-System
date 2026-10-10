"""Private procurement dispatch ledger; contains no platform credentials."""
from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ProcurementInputGroup(Base):
    __tablename__ = "procurement_input_groups"
    executor_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    input_group: Mapped[str] = mapped_column(String(64), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ProcurementAccountBinding(Base, TimestampMixin):
    __tablename__ = "procurement_account_bindings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    channel: Mapped[str] = mapped_column(String(24), nullable=False)
    identity_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    executor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    input_group: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="paused")
    new_24h: Mapped[int] = mapped_column(Integer, nullable=False, default=25)
    new_1h: Mapped[int] = mapped_column(Integer, nullable=False, default=6)
    messages_24h: Mapped[int] = mapped_column(Integer, nullable=False, default=40)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    __table_args__ = (
        UniqueConstraint("channel", "identity_hash", name="uq_procurement_account_identity"),
        CheckConstraint("new_24h BETWEEN 1 AND 25 AND new_1h BETWEEN 1 AND 6 AND messages_24h BETWEEN 1 AND 40", name="ck_procurement_account_caps"),
    )


class ProcurementTaskAccount(Base):
    __tablename__ = "procurement_task_accounts"
    task_id: Mapped[int] = mapped_column(ForeignKey("procurement_tasks.id", ondelete="RESTRICT"), primary_key=True)
    channel: Mapped[str] = mapped_column(String(24), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("procurement_account_bindings.id", ondelete="RESTRICT"), nullable=False)


class ProcurementContactClaim(Base):
    __tablename__ = "procurement_contact_claims"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("procurement_tasks.id", ondelete="RESTRICT"), nullable=False, index=True)
    inquiry_id: Mapped[int] = mapped_column(ForeignKey("procurement_inquiries.id", ondelete="RESTRICT"), nullable=False, unique=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("procurement_account_bindings.id", ondelete="RESTRICT"), nullable=False)
    merchant_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    attempted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("task_id", "merchant_hash", name="uq_procurement_contact_identity"),)


class ProcurementSendIntent(Base, TimestampMixin):
    __tablename__ = "procurement_send_intents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("procurement_tasks.id", ondelete="RESTRICT"), nullable=False, index=True)
    inquiry_id: Mapped[int] = mapped_column(ForeignKey("procurement_inquiries.id", ondelete="RESTRICT"), nullable=False)
    account_id: Mapped[str] = mapped_column(ForeignKey("procurement_account_bindings.id", ondelete="RESTRICT"), nullable=False, index=True)
    contact_id: Mapped[str] = mapped_column(ForeignKey("procurement_contact_claims.id", ondelete="RESTRICT"), nullable=False)
    action_key: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reserved_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    permit_hash: Mapped[Optional[str]] = mapped_column(String(64))
    permit_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reconcile_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reconcile_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    external_message_id: Mapped[Optional[str]] = mapped_column(String(255))
    observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint("task_id", "inquiry_id", "action_key", name="uq_procurement_intent_action"),
        UniqueConstraint("account_id", "external_message_id", name="uq_procurement_intent_receipt"),
        CheckConstraint("reconcile_attempts BETWEEN 0 AND 1", name="ck_procurement_reconcile_once"),
        CheckConstraint("state IN ('reserved','executing','unknown','reconciling','unresolved','confirmed_sent','cancelled')", name="ck_procurement_intent_state"),
    )
