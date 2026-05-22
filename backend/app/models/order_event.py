"""订单事件时间轴 (Phase 6, 借鉴 Stripe).

每个订单从创建到归档的每一步都写一条 OrderEvent — UI 上展示一个时间线,
"为什么这单变成这样" 3 秒看清。

写入点:
    - order_service.transition (状态变化)
    - factory_order_service.* (派生 / 作废)
    - inventory_lock_service.* (锁定 / 释放 / 出货)
    - return_service.* (退货流程)
    - 用户 comment / @ 提及 (后续 Tier 3)
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import JSON, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class OrderEvent(Base, TimestampMixin):
    __tablename__ = "order_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    # status_change / factory_order_generated / factory_order_voided
    # inventory_locked / inventory_released / consumed
    # refund_requested / aftersales_received / aftersales_inbound / aftersales_damaged
    # comment / mention / system_note / payment_received

    actor: Mapped[Optional[str]] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(String(256), nullable=False)
    # 时间线上显示的一句话, "客服张三 将订单从 待付款 改为 已付款"
    detail: Mapped[Optional[str]] = mapped_column(Text)
    context_json: Mapped[Optional[dict]] = mapped_column(JSON)
