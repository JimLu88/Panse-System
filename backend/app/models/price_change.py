"""价格/成本变更留痕 (优化 #5): 记录谁在何时把某 SKU 的价/成本从多少改成多少。"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class PriceChangeLog(Base, TimestampMixin):
    __tablename__ = "price_change_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku_code: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    old_value: Mapped[Optional[str]] = mapped_column(String(64))
    new_value: Mapped[Optional[str]] = mapped_column(String(64))
    actor: Mapped[Optional[str]] = mapped_column(String(64))
