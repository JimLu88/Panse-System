"""⑩ 线索/私信收件箱。对应 10-lead-inbox.md。"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..database import Base


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Lead(Base):
    """承接问询 + 来源归因 + 回写 ERP 成交。"""

    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 来源归因（小红书无外链 → 暗号 / 评论 / 组件 / 客服登记）
    source_type: Mapped[str] = mapped_column(String(20), default="dm")  # dm/comment/code/agent
    source_account_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_content_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attribution_code: Mapped[str] = mapped_column(String(40), default="")

    contact: Mapped[str] = mapped_column(String(80), default="")
    question: Mapped[str] = mapped_column(Text, default="")
    interest_category: Mapped[str] = mapped_column(String(50), default="")

    status: Mapped[str] = mapped_column(String(16), default="new")  # new/responded/quoting/won/lost
    erp_order_no: Mapped[str] = mapped_column(String(60), default="")  # 成交回写
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    last_touch_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
