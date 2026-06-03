from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import Date, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class PartInventory(Base, TimestampMixin):
    """配件库存 (Excel 表 4b-配件库存)。

    每条记录 = (仓库, 物料编码) 维度的库存快照。
    入库行 add_part_row 在物料缺失时会自动触发 Material 的「定制」建档。

    Phase 6: 数量改用 Decimal(14,3) — 之前 Integer 在 BOM 小数 qty 时会向上取整, 多锁/多扣库存.
    """

    __tablename__ = "part_inventory"

    id: Mapped[int] = mapped_column(primary_key=True)
    sync_key: Mapped[Optional[str]] = mapped_column(String(128), unique=True, nullable=True)
    warehouse: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    material_code: Mapped[str] = mapped_column(
        ForeignKey("materials.code", ondelete="RESTRICT"), nullable=False, index=True
    )
    spec: Mapped[Optional[str]] = mapped_column(String(255))
    unit: Mapped[Optional[str]] = mapped_column(String(16))
    physical_qty: Mapped[Decimal] = mapped_column(Numeric(14, 3), default=Decimal("0"), nullable=False)
    locked_qty: Mapped[Decimal] = mapped_column(Numeric(14, 3), default=Decimal("0"), nullable=False)
    last_inbound_at: Mapped[Optional[date]] = mapped_column(Date)
    last_outbound_at: Mapped[Optional[date]] = mapped_column(Date)
    safety_stock: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 3))
    remark: Mapped[Optional[str]] = mapped_column(String(255))

    @property
    def available_qty(self) -> Decimal:
        return Decimal(self.physical_qty or 0) - Decimal(self.locked_qty or 0)


class ProductInventory(Base, TimestampMixin):
    """成品库存 (Excel 表 4a-成品库存)。

    高级字段说明:
      safety_stock      — 手动设置或系统推算的安全库存量
      lead_time_days    — 工厂平均交货天数 (由 FactoryOrder 历史推算, 可手动覆盖)
      slow_moving_days  — 滞销预警阈值：超过此天数未出货 → 触发滞销警告 (默认 60)
      reorder_point     — 预警线：当 available_qty <= reorder_point 时触发补货预警
                          系统自动推算 = safety_stock + lead_time_days × daily_sales_30d
    """

    __tablename__ = "product_inventory"

    id: Mapped[int] = mapped_column(primary_key=True)
    sync_key: Mapped[Optional[str]] = mapped_column(String(128), unique=True, nullable=True)
    warehouse: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    product_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    sku: Mapped[Optional[str]] = mapped_column(String(255))
    spec: Mapped[Optional[str]] = mapped_column(String(255))
    unit: Mapped[Optional[str]] = mapped_column(String(16))
    physical_qty: Mapped[Decimal] = mapped_column(Numeric(14, 3), default=Decimal("0"), nullable=False)
    locked_qty: Mapped[Decimal] = mapped_column(Numeric(14, 3), default=Decimal("0"), nullable=False)
    safety_stock: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 3), nullable=True)
    lead_time_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    slow_moving_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=60)
    reorder_point: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 3), nullable=True)
    remark: Mapped[Optional[str]] = mapped_column(String(255))

    @property
    def available_qty(self) -> Decimal:
        return Decimal(self.physical_qty or 0) - Decimal(self.locked_qty or 0)
