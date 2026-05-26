"""审批请求 (Phase 9, Tier 2 #4).

业务: 订单 > 5000 / 库存调整 > 100 / 退款 > 1000 等高风险动作要主管审批.
状态机: pending → approved / rejected.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ApprovalRequest(Base, TimestampMixin):
    __tablename__ = "approval_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    # 'order_discount' / 'inventory_adjust' / 'refund' / 'price_change' / 'custom'
    target_table: Mapped[Optional[str]] = mapped_column(String(64))
    target_id: Mapped[Optional[int]] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    detail: Mapped[Optional[str]] = mapped_column(Text)
    payload_json: Mapped[Optional[dict]] = mapped_column(JSON)
    # 待审批的具体改动 (target/diff), approve 时执行

    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False, index=True)
    # pending / approved / rejected / cancelled

    requested_by: Mapped[str] = mapped_column(String(64), nullable=False)
    approver: Mapped[Optional[str]] = mapped_column(String(64))
    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reject_reason: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (
        Index("ix_approval_status_kind", "status", "kind"),
    )
