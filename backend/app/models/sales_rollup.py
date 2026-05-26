"""销售日汇总物化表 (Phase 12 P3-13).

业务: 当订单量上到 5w 时, /reports/sales/summary 每次全表 scan 太慢.
解决方案: 每天 06:30 调度器把昨日订单按 (date, product, sku) 聚合, 写入 sales_daily_rollup.
查询时直接 SUM rollup 表 (30 行 = 30 天), 而不是 SUM 5w 订单。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import Date, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class SalesDailyRollup(Base, TimestampMixin):
    __tablename__ = "sales_daily_rollup"

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    product_code: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    sku_code: Mapped[Optional[str]] = mapped_column(String(32))
    platform: Mapped[Optional[str]] = mapped_column(String(32))

    order_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    qty: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    revenue: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"), nullable=False)
    cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"), nullable=False)
    net_profit: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"), nullable=False)

    __table_args__ = (
        UniqueConstraint("day", "product_code", "sku_code", "platform",
                         name="uq_rollup_dim"),
        Index("ix_rollup_day_product", "day", "product_code"),
    )
