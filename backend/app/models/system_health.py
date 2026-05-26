"""系统监控日志 (业务需求: 看门狗).

每 60s tick 一次, 记录每项 health check 的状态。
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class SystemHealthLog(Base, TimestampMixin):
    __tablename__ = "system_health_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    check_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # ok / warn / fail
    detail: Mapped[Optional[str]] = mapped_column(Text)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)

    __table_args__ = (
        Index("ix_system_health_logs_check_status", "check_name", "status"),
    )
