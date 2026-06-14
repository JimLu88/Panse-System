"""系统域：健康体检日志（看门狗写入）。"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class HealthLog(Base):
    """看门狗 60s 体检记录。"""

    __tablename__ = "system_health_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ok: Mapped[bool] = mapped_column(default=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    checked_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
