"""竞品价目 (表格二 → 微定制旁边并排参考)。"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from sqlalchemy import Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class CompetitorPrice(Base, TimestampMixin):
    __tablename__ = "competitor_prices"

    id: Mapped[int] = mapped_column(primary_key=True)
    store: Mapped[Optional[str]] = mapped_column(String(128), index=True)      # 店铺名
    category: Mapped[Optional[str]] = mapped_column(String(64), index=True)    # 类目
    product: Mapped[Optional[str]] = mapped_column(String(256))               # 产品
    link: Mapped[Optional[str]] = mapped_column(Text)                          # 链接
    wood: Mapped[Optional[str]] = mapped_column(String(64))                    # 木材
    sku_name: Mapped[Optional[str]] = mapped_column(String(256), index=True)   # SKU 名
    daily_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))     # 日常活动价
