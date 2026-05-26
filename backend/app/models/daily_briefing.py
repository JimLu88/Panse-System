"""AI 每日经营简报 (Phase 6, 业务需求 8.1).

每天 09:00 调度器跑一次, AI 把昨天的关键数据合成一段话:
    - 销售环比
    - 库存风险点 (即将断货)
    - 利润亮点 / 滞销提醒
    - 推荐动作

UI 在首页顶部显示 + 推企业微信群。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import JSON, Date, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class DailyBriefing(Base, TimestampMixin):
    __tablename__ = "daily_briefings"

    id: Mapped[int] = mapped_column(primary_key=True)
    for_date: Mapped[date] = mapped_column(Date, nullable=False, unique=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    highlights_json: Mapped[Optional[dict]] = mapped_column(JSON)
    # [{"kind": "risk"/"opportunity"/"action", "title": "...", "url": "..."}]
    model: Mapped[Optional[str]] = mapped_column(String(64))
    generated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
