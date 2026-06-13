"""人工编辑历史档案 (用户方向 2+4) — 字段级修改流水。

只记"人"的改动 (web 编辑 / 飞书修改), 系统自动重算不记。
每条 = 某表某行某字段的一次值变化, 含修改人/来源/时间, 永久保留。
前端: 字段悬浮历史 (最近 30 份) + 修改档案中心 (全局检索)。
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class FieldChange(Base, TimestampMixin):
    __tablename__ = "field_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    table_name: Mapped[str] = mapped_column(String(64), nullable=False)
    row_pk: Mapped[str] = mapped_column(String(64), nullable=False)      # 业务主键 (sku_code/订单号/id)
    row_label: Mapped[Optional[str]] = mapped_column(String(255))        # 行的人话描述 (产品名等)
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    field_label: Mapped[Optional[str]] = mapped_column(String(64))       # 字段中文名
    old_value: Mapped[Optional[str]] = mapped_column(Text)
    new_value: Mapped[Optional[str]] = mapped_column(Text)
    actor: Mapped[Optional[str]] = mapped_column(String(64))             # 账号名
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="web")  # web / feishu

    __table_args__ = (
        Index("ix_field_changes_target", "table_name", "row_pk", "field"),
        Index("ix_field_changes_created", "created_at"),
    )
