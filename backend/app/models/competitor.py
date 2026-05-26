"""竞品价目 (表格二 → 微定制旁边并排参考)。"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import DateTime, Numeric, String, Text
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
    daily_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))     # 我表记录价(叠券前)
    # 通过链接抓取的最新价 (淘宝反爬, 尽力抓; 也可手动更新)
    latest_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    latest_fetched_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    fetch_status: Mapped[Optional[str]] = mapped_column(String(16))            # ok/blocked/failed/manual
