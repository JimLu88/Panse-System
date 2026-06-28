from decimal import Decimal
from typing import Optional

from sqlalchemy import ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class BomLine(Base, TimestampMixin):
    """BOM 表 (Excel 表 3-BOM表) — Phase 1 占位骨架。"""

    __tablename__ = "bom_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    sync_key: Mapped[Optional[str]] = mapped_column(String(128), unique=True, nullable=True)
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
    remark: Mapped[Optional[str]] = mapped_column(Text)   # 备注(尺寸/工艺说明, 可能很长 → Text 不限长)
    product_name: Mapped[Optional[str]] = mapped_column(String(255))   # 产品名称 (冗余, 方便对账)
    material_name: Mapped[Optional[str]] = mapped_column(String(255))  # 物料名称 (冗余, 方便对账)
    # AI 推演/人工确认的尺寸串(如 "1800*800"), 不覆盖原 remark(配件 epic 阶段1; 用户 2026-06-28)。
    # 计算面积时 remark 优先、缺则用 est_size; 多单 BOM 用量占比分摊成本时用此尺寸。
    est_size: Mapped[Optional[str]] = mapped_column(String(128))
    # 'inferred'=AI预估(可改) | 'confirmed'=人工确认(前端二次确认过) | NULL=未推演
    size_status: Mapped[Optional[str]] = mapped_column(String(16))
