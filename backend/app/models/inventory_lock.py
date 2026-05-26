"""库存锁定流水 (Phase 1C).

业务需求 3/10/11: 创建工厂订单时锁库存 (available_qty 不减少 physical, 只 +locked_qty);
取消订单 → release; 实际出货 → consume (physical -= qty, locked -= qty);
退货入库 → return_in (physical += qty).

整条 ledger 是 append-only 审计 trail, 即使 PartInventory.locked_qty 出错, 也能拉 ledger 倒推。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from sqlalchemy import ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class InventoryLockLedger(Base, TimestampMixin):
    __tablename__ = "inventory_lock_ledger"

    id: Mapped[int] = mapped_column(primary_key=True)

    # 操作主体: 工厂订单 / 平台订单 / 售后单 / 盘点 / 手动
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # 'factory_order' / 'platform_order' / 'aftersales' / 'manual' / 'count'
    source_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)

    # 物料级或成品级 (二者必填其一)
    material_code: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    product_code: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    sku_code: Mapped[Optional[str]] = mapped_column(String(64))

    warehouse: Mapped[Optional[str]] = mapped_column(String(64), default="default")

    # 操作类型
    kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # 'lock' (锁定 +qty)
    # 'release' (释放 -qty, 订单取消)
    # 'consume' (实际出货, 同时 -locked -physical)
    # 'inbound' (入库 +physical)
    # 'outbound_unlocked' (无锁直接出, -physical)
    # 'return_in' (退货入库 +physical)
    # 'count_adjust' (盘点调整 ±physical)

    qty: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    # 永远 ≥ 0; 方向由 kind 决定

    actor: Mapped[Optional[str]] = mapped_column(String(64))
    # 用户名 / 'system' / 'scheduler'
    remark: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (
        Index("ix_inventory_lock_source", "source_kind", "source_id"),
        Index("ix_inventory_lock_material_kind", "material_code", "kind"),
    )
