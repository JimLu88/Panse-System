"""AI 辅助系统的两张审计表 (plan §3.2)。"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class AiChatLog(Base, TimestampMixin):
    """每次 AI 调用的完整记录 (plan §7.2 安全边界：每次 AI 交互都有完整日志)。"""

    __tablename__ = "ai_chat_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(64))
    session_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # diagnose / chat / propose_smoothing / code_patch
    related_exception_id: Mapped[Optional[int]] = mapped_column(ForeignKey("data_exceptions.id"))
    user_message: Mapped[Optional[str]] = mapped_column(Text)
    ai_response: Mapped[Optional[str]] = mapped_column(Text)
    model: Mapped[Optional[str]] = mapped_column(String(64))
    input_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    output_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    cache_read_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    cache_creation_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    error: Mapped[Optional[str]] = mapped_column(Text)
    extra: Mapped[Optional[dict]] = mapped_column(JSON)


class AiCodePatch(Base, TimestampMixin):
    """AI 代码修复留痕 (plan §3.2 ai_code_patches)。

    Phase 3.5 仅建表 + diff 记录路径；实际 apply / rollback 留 Phase 6 做（必须管理员审批）。
    """

    __tablename__ = "ai_code_patches"

    id: Mapped[int] = mapped_column(primary_key=True)
    trigger_exception_id: Mapped[Optional[int]] = mapped_column(ForeignKey("data_exceptions.id"))
    file_path: Mapped[str] = mapped_column(String(255), nullable=False)
    diff_content: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="proposed", nullable=False, index=True)
    # proposed / approved / applied / rejected / rolled_back
    approved_by: Mapped[Optional[str]] = mapped_column(String(64))
    applied_at: Mapped[Optional[str]] = mapped_column(String(32))
    rollback_at: Mapped[Optional[str]] = mapped_column(String(32))
