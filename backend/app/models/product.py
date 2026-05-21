from typing import Optional

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Product(Base, TimestampMixin):
    """产品总表 (Excel 表 1-产品总表) — Phase 1 占位骨架，最小字段集。"""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    brand: Mapped[Optional[str]] = mapped_column(String(32))
    category: Mapped[Optional[str]] = mapped_column(String(64))
    remark: Mapped[Optional[str]] = mapped_column(String(255))

    # 淘宝商品 ID (主) + 备选 ID 列表 (因链接会换, 业务需求 §4)
    taobao_id: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    alt_taobao_ids: Mapped[Optional[list]] = mapped_column(JSON, default=list)
