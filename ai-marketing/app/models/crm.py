"""客户库（第一方数据）+ A/B 实验。对标 2026 first-party data + 实验平台趋势。"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Customer(Base):
    """客户：从成交线索 + ERP 订单聚合，做 RFM 分层与复购触达。"""

    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contact: Mapped[str] = mapped_column(String(80), index=True)
    source: Mapped[str] = mapped_column(String(20), default="xhs")  # 来源渠道
    first_order_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    last_order_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    order_count: Mapped[int] = mapped_column(Integer, default=0)
    total_amount: Mapped[float] = mapped_column(Float, default=0.0)
    rfm_tier: Mapped[str] = mapped_column(String(16), default="new")  # vip/repeat/normal/sleeping/new
    tags: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class Experiment(Base):
    """A/B 实验（封面/标题/钩子/发布时间），多臂老虎机择优。"""

    __tablename__ = "experiments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    factor: Mapped[str] = mapped_column(String(20), default="title")  # title/cover/hook/time
    arms: Mapped[list] = mapped_column(JSON, default=list)   # [{name, impressions, reward}]
    status: Mapped[str] = mapped_column(String(12), default="running")  # running/done
    winner: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
