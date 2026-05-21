from decimal import Decimal
from typing import Optional

from sqlalchemy import ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class BomLine(Base, TimestampMixin):
    """BOM 表 (Excel 表 3-BOM表) — Phase 1 占位骨架。"""

    __tablename__ = "bom_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    sku: Mapped[Optional[str]] = mapped_column(String(255))
    sku_code: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    material_code: Mapped[str] = mapped_column(
        ForeignKey("materials.code", ondelete="RESTRICT"), nullable=False, index=True
    )
    unit: Mapped[Optional[str]] = mapped_column(String(16))
    qty_per_product: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("1"))
    # 尺寸类型 (业务需求 §2: 决定微定制时这行 BOM 是否随尺寸变 — "组合"= 随; "个数"= 不变)
    size_type: Mapped[Optional[str]] = mapped_column(String(32))
    remark: Mapped[Optional[str]] = mapped_column(String(255))
