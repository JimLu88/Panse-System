"""会计期间 (Phase 6, 借鉴 SAP/NetSuite).

业务: 月底关账后, 该月任何订单 / 财务记录都不允许改 (除 admin 重新打开).
防止 "上个月利润又被改了" 的混乱。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class AccountingPeriod(Base, TimestampMixin):
    __tablename__ = "accounting_periods"

    id: Mapped[int] = mapped_column(primary_key=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False)
    # open: 可改; closed: 不能改但 admin 可重开; locked: 锁死 (年审后)

    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[Optional[str]] = mapped_column(String(64))
    remark: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("year", "month", name="uq_accounting_periods_ym"),
    )
