"""飞书双向同步主键映射 (plan §5.3)。

每条记录 = 系统某条业务记录 ↔ 飞书多维表格某行 的对应关系。
hash 字段用来在每次同步前判断「两边自上次同步是否被改动过」，
若两边都改了 → 写入冲突表（Phase 1 暂只占位）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class FeishuSyncMap(Base, TimestampMixin):
    __tablename__ = "feishu_sync_map"

    id: Mapped[int] = mapped_column(primary_key=True)
    system_table: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    system_pk: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    feishu_app_token: Mapped[str] = mapped_column(String(64), nullable=False)
    feishu_table_id: Mapped[str] = mapped_column(String(64), nullable=False)
    feishu_record_id: Mapped[str] = mapped_column(String(64), nullable=False)
    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    system_hash: Mapped[Optional[str]] = mapped_column(String(64))
    feishu_hash: Mapped[Optional[str]] = mapped_column(String(64))

    __table_args__ = (
        UniqueConstraint("system_table", "system_pk", name="uq_feishu_sync_system"),
        UniqueConstraint(
            "feishu_app_token", "feishu_table_id", "feishu_record_id", name="uq_feishu_sync_remote"
        ),
    )


class FeishuTableBinding(Base, TimestampMixin):
    """系统表 ↔ 飞书多维表 的全局绑定（不是行级映射）。"""

    __tablename__ = "feishu_table_bindings"

    id: Mapped[int] = mapped_column(primary_key=True)
    system_table: Mapped[str] = mapped_column(String(64), nullable=False)
    feishu_app_token: Mapped[str] = mapped_column(String(64), nullable=False)
    feishu_table_id: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[str] = mapped_column(String(16), default="bidirectional")  # in / out / bidirectional
    field_mapping: Mapped[Optional[str]] = mapped_column(String(2048))  # JSON string
    enabled: Mapped[bool] = mapped_column(default=False, nullable=False)

    __table_args__ = (
        UniqueConstraint("system_table", "feishu_table_id", name="uq_feishu_binding_table_pair"),
    )
