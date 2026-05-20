from datetime import date
from typing import Optional

from sqlalchemy import Date, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class PartInventory(Base, TimestampMixin):
    """配件库存 (Excel 表 4b-配件库存)。

    每条记录 = (仓库, 物料编码) 维度的库存快照。
    入库行 add_part_row 在物料缺失时会自动触发 Material 的「定制」建档。
    """

    __tablename__ = "part_inventory"

    id: Mapped[int] = mapped_column(primary_key=True)
    warehouse: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    material_code: Mapped[str] = mapped_column(
        ForeignKey("materials.code", ondelete="RESTRICT"), nullable=False, index=True
    )
    spec: Mapped[Optional[str]] = mapped_column(String(255))
    unit: Mapped[Optional[str]] = mapped_column(String(16))
    physical_qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_inbound_at: Mapped[Optional[date]] = mapped_column(Date)
    last_outbound_at: Mapped[Optional[date]] = mapped_column(Date)
    safety_stock: Mapped[Optional[int]] = mapped_column(Integer)
    remark: Mapped[Optional[str]] = mapped_column(String(255))

    @property
    def available_qty(self) -> int:
        return (self.physical_qty or 0) - (self.locked_qty or 0)


class ProductInventory(Base, TimestampMixin):
    """成品库存 (Excel 表 4a-成品库存)。"""

    __tablename__ = "product_inventory"

    id: Mapped[int] = mapped_column(primary_key=True)
    warehouse: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    product_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    sku: Mapped[Optional[str]] = mapped_column(String(255))
    spec: Mapped[Optional[str]] = mapped_column(String(255))
    unit: Mapped[Optional[str]] = mapped_column(String(16))
    physical_qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    remark: Mapped[Optional[str]] = mapped_column(String(255))
