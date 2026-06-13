from decimal import Decimal
from typing import Optional

from sqlalchemy import JSON, Boolean, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Material(Base, TimestampMixin):
    """物料单价库 (Excel 表 3b-配件价格表)。

    编码规则：<前缀>-<4位序号>。前缀含义：
        AC = 配件 (accessory)
        MP = 人工费
        MW = 木材
        SP = 特殊件
    序号约定（仅 AC 前缀）：
        0001-0999 = 标准物料
        1000+     = 定制物料 (custom)，名称约定以「定制」开头
    """

    __tablename__ = "materials"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    size_type: Mapped[Optional[str]] = mapped_column(String(32))
    unit: Mapped[Optional[str]] = mapped_column(String(16))
    price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    remark: Mapped[Optional[str]] = mapped_column(String(255))
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Phase 4 智能提前备货 / 库存预警用
    lead_time_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # 开料 + 物流总周期, 倒推预警触发点
    priority: Mapped[str] = mapped_column(String(8), default="mid", nullable=False)
    # high / mid / low

    # Phase 6: 停产 + 供应商多元化 (备用供应商防断货)
    is_discontinued: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    primary_supplier_id: Mapped[Optional[int]] = mapped_column(Integer)
    alt_supplier_ids: Mapped[Optional[list]] = mapped_column(JSON, default=list)

    # 微定制面积计算字段 (Phase 13)
    area: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4))    # 单件面积 m²
    width_mm: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))
    height_mm: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2))

    # Plan C3 防串料: 定制件记录它从哪个基础物料派生 (复用判定按它精确对照)
    base_material_code: Mapped[Optional[str]] = mapped_column(String(64), index=True)

    __table_args__ = (
        Index("ix_materials_name_unique", "name", unique=True),
    )

    @property
    def prefix(self) -> str:
        return self.code.split("-", 1)[0] if "-" in self.code else ""

    @property
    def serial(self) -> int:
        try:
            return int(self.code.split("-", 1)[1])
        except (IndexError, ValueError):
            return 0
