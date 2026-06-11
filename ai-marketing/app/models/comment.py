"""⑧ 评论引流机会。对应 08-comment-engine.md。"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class CommentOpportunity(Base):
    """上升期笔记 → 匹配产品线 → AI 草拟评论 → 人工点发（永不全自动）。"""

    __tablename__ = "comment_opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    note_url: Mapped[str] = mapped_column(String(300), default="")
    note_title: Mapped[str] = mapped_column(String(200))
    note_kind: Mapped[str] = mapped_column(String(20), default="general")  # decor_diary/general
    growth_rate: Mapped[float] = mapped_column(Float, default=0.0)  # 上升期互动增速
    match_category: Mapped[str] = mapped_column(String(50), default="")
    match_score: Mapped[float] = mapped_column(Float, default=0.0)

    comment_kind: Mapped[str] = mapped_column(String(10), default="seed")  # seed(种水)/guide(引导)
    draft_comment: Mapped[str] = mapped_column(Text, default="")
    compliance: Mapped[dict] = mapped_column(JSON, default=dict)
    suggested_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending/posted/skipped
    posted_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    posted_by_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
