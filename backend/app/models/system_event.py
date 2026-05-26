"""系统事件 (重启 / 看门狗触发 / 进程启动) — 业务需求 5.

每次重启会留下:
    restart_requested (admin 点了按钮 / 看门狗触发)
    process_started   (新进程启动后第一件事)
不同 kind 共享 snapshot_json (重启前后状态快照, 用来在 UI 上展示 diff)。
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import JSON, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class SystemEvent(Base, TimestampMixin):
    __tablename__ = "system_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    # restart_requested / restart_completed / watchdog_triggered / process_started /
    # orphan_killed / restart_failed
    actor: Mapped[Optional[str]] = mapped_column(String(64))  # 用户名 或 'watchdog' 或 'system'
    detail: Mapped[Optional[str]] = mapped_column(Text)
    snapshot_json: Mapped[Optional[dict]] = mapped_column(JSON)
    # 通常是 SystemStatus 的精简版 (mem%, disk%, db_ok, uptime)

    __table_args__ = (
        Index("ix_system_events_kind_created", "kind", "created_at"),
    )
