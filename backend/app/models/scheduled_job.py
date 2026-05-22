"""定时任务执行日志 (Phase 1A).

记录 APScheduler 每次跑任务的结果, 用于:
    - admin UI "全自动任务清单" 展示
    - 失败重试 / 排查
    - 功能 18 "告知我多久完成一次" 的数据源
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ScheduledJobRun(Base, TimestampMixin):
    __tablename__ = "scheduled_job_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # APScheduler 注册的 job id, 如 "daily_17_refund_check" / "watchdog_tick"
    job_label: Mapped[str] = mapped_column(String(128), nullable=False)
    # 中文展示名, 给 UI 用

    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # ok / fail / skipped

    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    detail: Mapped[Optional[str]] = mapped_column(Text)
    error: Mapped[Optional[str]] = mapped_column(Text)
    result_summary: Mapped[Optional[dict]] = mapped_column(JSON)
    # 业务结果, 如 {"alerts_created": 3, "orders_cancelled": 2}

    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_scheduled_job_runs_job_status", "job_id", "status"),
    )
