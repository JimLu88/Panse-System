"""内容域模型：选题 / 草稿 / 内容事件流。

对应 03-data-model/unified-content-model.md：一条内容是一串不可变事件。
MVP 用关系表存当前状态快照 + 一张事件流表留痕（事件溯源的轻量落地）。
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Topic(Base):
    """① 选题对象。"""

    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(50), index=True)
    platform_targets: Mapped[list] = mapped_column(JSON, default=list)
    heat_score: Mapped[int] = mapped_column(Integer, default=50)
    heat_status: Mapped[str] = mapped_column(String(10), default="safe")  # peak/safe/decay
    topic_kind: Mapped[str] = mapped_column(String(10), default="trend")  # trend(时效)/evergreen(常青)
    keywords: Mapped[list] = mapped_column(JSON, default=list)
    recommended_style: Mapped[str] = mapped_column(String(50), default="diary")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)

    drafts: Mapped[list["Draft"]] = relationship(back_populates="topic")


class Draft(Base):
    """③ 生成草稿对象（含四层流水线产物 + 必改3节点）。"""

    __tablename__ = "drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"))
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    narrative_units: Mapped[list] = mapped_column(JSON, default=list)

    fact_check: Mapped[dict] = mapped_column(JSON, default=dict)
    compliance: Mapped[dict] = mapped_column(JSON, default=dict)
    ai_likeness: Mapped[int] = mapped_column(Integer, default=0)  # 0-100 越低越像人
    info_density: Mapped[float] = mapped_column(Float, default=0.0)
    must_fix: Mapped[dict] = mapped_column(JSON, default=dict)
    lineage: Mapped[dict] = mapped_column(JSON, default=dict)

    status: Mapped[str] = mapped_column(String(20), default="drafted")  # drafted/approved/rejected/published
    review_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)

    topic: Mapped["Topic"] = relationship(back_populates="drafts")


class ContentEvent(Base):
    """事件流：内容生命周期每一步留痕（唯一真相源的轻量版）。"""

    __tablename__ = "content_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_id: Mapped[int] = mapped_column(Integer, index=True)  # = draft id
    event_type: Mapped[str] = mapped_column(String(40))  # topic_chosen/draft_generated/...
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
