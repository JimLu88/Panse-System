"""导入原始文件归档 (用户需求: 每次导入的图片和表格自动分类文件夹存储, 可回溯)。

每条 = 一次导入上传的原始文件 (表格/图片) 的归档记录。
物理文件由 import_storage 落盘到 {STORAGE_ROOT}/imports/{kind}/{年}/{月}/{uuid}.{ext}。
file_hash (sha256) 用于"同一文件重复上传"提示。
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import JSON, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ImportedFile(Base, TimestampMixin):
    __tablename__ = "imported_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    # orders/taobao/alipay/settlement/wanshifu/logistics/promotion/aftersales/refill/account_balance/generic
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    original_filename: Mapped[Optional[str]] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_hash: Mapped[Optional[str]] = mapped_column(String(64), index=True)  # sha256
    size_bytes: Mapped[Optional[int]] = mapped_column(Integer)
    content_type: Mapped[Optional[str]] = mapped_column(String(128))
    source: Mapped[str] = mapped_column(String(16), default="web", nullable=False)  # web/email/api
    import_job_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    # 导入结果摘要 {inserted, updated, skipped_duplicate, skipped_invalid, ...}
    row_summary: Mapped[Optional[dict]] = mapped_column(JSON)
    uploaded_by: Mapped[Optional[str]] = mapped_column(String(64))
