"""采集域：竞品爆文 / 自有笔记评论 / 品牌舆情。

数据来自 data_source（接真实采集走爬虫，否则演示数据）。采集只读，
发布/评论永远人工——风险隔离，与系统设计哲学一致。
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class HotNote(Base):
    """竞品/同赛道爆文（#6 爆文挖掘 / #7 低粉爆文嗅探 / #10 评论词云源）。"""

    __tablename__ = "hot_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(10), default="xhs")
    title: Mapped[str] = mapped_column(String(200))
    author: Mapped[str] = mapped_column(String(80), default="")
    author_followers: Mapped[int] = mapped_column(Integer, default=0)
    likes: Mapped[int] = mapped_column(Integer, default=0)
    collects: Mapped[int] = mapped_column(Integer, default=0)
    comments_count: Mapped[int] = mapped_column(Integer, default=0)
    category: Mapped[str] = mapped_column(String(50), default="")
    cover_style: Mapped[str] = mapped_column(String(80), default="")   # 封面风格拆解
    structure: Mapped[str] = mapped_column(String(120), default="")    # 结构骨架拆解
    is_low_fan_hit: Mapped[bool] = mapped_column(default=False)        # 低粉爆文(内容赢)
    sample_comments: Mapped[list] = mapped_column(JSON, default=list)  # 评论样本(词云源)
    url: Mapped[str] = mapped_column(String(300), default="")
    crawled_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class InboundComment(Base):
    """自有笔记下的评论（#17 评论管理 / #18 意图分类 / #20 楼中楼）。"""

    __tablename__ = "inbound_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_id: Mapped[int] = mapped_column(Integer, index=True)   # 我们的稿 id
    account_id: Mapped[int] = mapped_column(Integer, index=True)   # 发布该笔记的号
    author: Mapped[str] = mapped_column(String(80), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    intent: Mapped[str] = mapped_column(String(16), default="other")  # price/size/material/praise/complaint/other
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 楼中楼父评论
    reply_draft: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(12), default="new")  # new/replied/converted
    lead_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class BrandMention(Base):
    """品牌/竞品舆情（#19）。"""

    __tablename__ = "brand_mentions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform: Mapped[str] = mapped_column(String(10), default="xhs")
    mention_type: Mapped[str] = mapped_column(String(12), default="brand")  # brand/competitor
    keyword: Mapped[str] = mapped_column(String(40), default="")
    note_title: Mapped[str] = mapped_column(String(200), default="")
    snippet: Mapped[str] = mapped_column(Text, default="")
    sentiment: Mapped[str] = mapped_column(String(10), default="neutral")  # pos/neg/neutral
    url: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(12), default="new")  # new/handled
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
