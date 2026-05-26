from typing import Optional

from sqlalchemy import JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class DataException(Base, TimestampMixin):
    """异常处理与建议模块的核心表 (plan §3.2 / §6)。

    severity: info / warning / error
    status:   open / resolved / ignored
    """

    __tablename__ = "data_exceptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_table: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_pk: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    exception_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), default="warning", nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    suggestion_action: Mapped[Optional[str]] = mapped_column(String(64))
    context: Mapped[Optional[dict]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="open", nullable=False, index=True)
    resolved_by: Mapped[Optional[str]] = mapped_column(String(64))
    resolved_at: Mapped[Optional[str]] = mapped_column(String(32))
    ai_analysis: Mapped[Optional[str]] = mapped_column(Text)
    # 严重度自动升级 (plan §12.1)
    escalation_count: Mapped[int] = mapped_column(default=0, nullable=False)
    last_escalated_at: Mapped[Optional[str]] = mapped_column(String(32))
