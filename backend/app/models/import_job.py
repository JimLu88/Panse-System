"""异步导入作业 (业务需求 6: 大文件异步处理).

100MB 的对账 Excel 同步处理会超 nginx 超时, 改异步:
    1) POST /api/importer/commit-async → 返回 job_id, 后台 ThreadPoolExecutor 跑
    2) 前端每 2s 轮询 /api/importer/jobs/{id} 拿 progress
    3) 完成 / 失败 → 拉报告
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ImportJob(Base, TimestampMixin):
    __tablename__ = "import_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    sheet_name: Mapped[str] = mapped_column(String(128), nullable=False)
    mapping: Mapped[Optional[dict]] = mapped_column(JSON)
    options_json: Mapped[Optional[dict]] = mapped_column(JSON)

    # 大文件 (100MB+) 不再常驻内存; worker 从 file_path 读, 跑完删掉
    file_path: Mapped[Optional[str]] = mapped_column(Text)

    total_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processed_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False, index=True)
    # pending / running / done / failed / cancelled

    # 用户点 "取消" 设置为 True; worker 在下一次 progress tick 自检退出
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    error: Mapped[Optional[str]] = mapped_column(Text)
    report: Mapped[Optional[dict]] = mapped_column(JSON)

    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
