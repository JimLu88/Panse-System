"""分发域：发布事件 + 回收指标。对应 06-dispatcher.md / 07-analytics.md。"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class PublishEvent(Base):
    """⑥ 发布事件（含反共振错峰参数）。"""

    __tablename__ = "publish_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_id: Mapped[int] = mapped_column(ForeignKey("drafts.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    platform: Mapped[str] = mapped_column(String(10), default="xhs")
    scheduled_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    driver_used: Mapped[str] = mapped_column(String(12), default="assist")
    result: Mapped[str] = mapped_column(String(12), default="pending")  # pending/success/failed
    offset_minutes: Mapped[int] = mapped_column(Integer, default=0)  # 反共振错峰
    tag_variant: Mapped[list] = mapped_column(JSON, default=list)


class Metric(Base):
    """⑦ 回收指标 + 真实感分数。"""

    __tablename__ = "metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_id: Mapped[int] = mapped_column(Integer, index=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    checkpoint: Mapped[str] = mapped_column(String(10), default="T+24h")
    views: Mapped[int] = mapped_column(Integer, default=0)
    likes: Mapped[int] = mapped_column(Integer, default=0)
    comments: Mapped[int] = mapped_column(Integer, default=0)
    collects: Mapped[int] = mapped_column(Integer, default=0)
    realness_score: Mapped[float] = mapped_column(Float, default=0.0)
    weight_factor: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
