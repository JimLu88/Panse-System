"""中央物流追踪表 (shipments) — 任何带快递单号的业务实体都挂这里。

单一数据源: 不在 5 张业务表各加 carrier/events/status 缓存列, 而是统一一行
(entity_type, entity_id, tracking_no)。实时查询结果缓存在此, 并由 shipment_service
把派生状态 (签收→订单签收 / 售后二次入库) 回写到对应业务表。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

# 业务实体类型 → 物流场景
SHIPMENT_ENTITY_TYPES = (
    "order",                # 订单出库 (Order.tracking_no)
    "after_sales_refill",   # 售后补发件 (AfterSales.refill_tracking_no)
    "after_sales_return",   # 售后退货/返厂 (AfterSales.return_tracking_no)
    "factory_order",        # 工厂发货入仓 (FactoryOrder.tracking_no)
    "refill_record",        # 补单发货 (RefillRecord.tracking_no)
    "part_purchase",        # 配件采购入库 (PartPurchase.tracking_no)
)


class Shipment(Base, TimestampMixin):
    __tablename__ = "shipments"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    tracking_no: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    carrier_code: Mapped[Optional[str]] = mapped_column(String(64))   # provider 承运商代码
    carrier_name: Mapped[Optional[str]] = mapped_column(String(64))   # 顺丰/中通...
    provider: Mapped[Optional[str]] = mapped_column(String(16))       # kuaidi100 / kdniao
    state: Mapped[Optional[str]] = mapped_column(String(16))          # provider state 码
    mapped_status: Mapped[Optional[str]] = mapped_column(String(32))  # 归一化: 运输中 / 已到货
    is_signed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_status: Mapped[Optional[str]] = mapped_column(String(255))   # 最新一条轨迹
    events: Mapped[Optional[list]] = mapped_column(JSON)              # 缓存时间线 [{time, context}]
    # 是否仍需轮询: 签收后置 False, 不再耗用 API 额度
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    last_error: Mapped[Optional[str]] = mapped_column(String(255))
    queried_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", "tracking_no", name="uq_shipment_entity_no"),
        Index("ix_shipments_active_entity", "active", "entity_type"),
    )
