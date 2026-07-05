# -*- coding: utf-8 -*-
"""ChatBI 问答审计表 (Plan4 v2 §4.8)。

每次问数落一行: 问句 / 路由 / SQL / 行数 / 耗时 / 状态 / 反馈。既做审计留痕(安全六道闸第5道),
也做反馈飞轮语料源 (👍 的问答 → few-shot / 晋升模板候选)。
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

# 路由类型: template=模板 / semi=半生成 / generated=AI直出 / refused=拒答 / clarify=澄清
CHATBI_ROUTES = ("template", "semi", "generated", "refused", "clarify")


class ChatbiQuery(Base, TimestampMixin):
    __tablename__ = "chatbi_queries"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    route: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    template_key: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    sql_text: Mapped[Optional[str]] = mapped_column(Text)
    sql_fingerprint: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    row_count: Mapped[Optional[int]] = mapped_column(Integer)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="ok", nullable=False, index=True)  # ok/error/refused
    error: Mapped[Optional[str]] = mapped_column(Text)
    llm_model: Mapped[Optional[str]] = mapped_column(String(64))
    feedback: Mapped[Optional[str]] = mapped_column(String(8))     # up / down / None
    feedback_note: Mapped[Optional[str]] = mapped_column(Text)
