"""AI 常见问题库 (plan §12.2)。

每次 AI 诊断之前先看这表有没有 (exception_type, context_hash) 命中。
命中就直接返回 solution_text + 增加 usage_count，不重复打 API。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class AiKnowledge(Base, TimestampMixin):
    __tablename__ = "ai_knowledge"

    id: Mapped[int] = mapped_column(primary_key=True)
    exception_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    context_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # AI 提供的解决方案 (复用)
    solution_text: Mapped[str] = mapped_column(Text, nullable=False)
    # 来源信息 — 第一次产生这条知识时的异常 id, 用于追溯
    source_exception_id: Mapped[Optional[int]] = mapped_column(Integer)
    source_description: Mapped[Optional[str]] = mapped_column(Text)
    model: Mapped[Optional[str]] = mapped_column(String(64))
    usage_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("exception_type", "context_hash", name="uq_ai_knowledge_key"),
    )
