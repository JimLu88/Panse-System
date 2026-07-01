"""定价版本历史 (工厂调价历史表, 2026-07-01 用户拍板)。

用户诉求: 工厂/销售价谈判变动只影响后续订单——调价时选"生效日 D", D 之前的订单按老成本/老价、
D 之后按新价, 历史利润不被追溯改写。

实现: 每次"带生效日"的调价, 把**改动前(旧)**的定价值快照成一条【已关闭区间】 [period_start, period_end=D),
live pricing_sku 保持"当前区间"[最后一个 D, ∞)。订单按其 order_date 落在哪个区间取对应成本:
  order_date >= 最后一个 D  → 用 live pricing_sku (现值)
  order_date <  最后一个 D  → 用命中区间的版本快照
无任何版本行 → 完全回退 live (=改造前行为, 不影响存量数字)。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Date, DateTime, Index, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# 首个区间的起点 (老价"自古以来"适用于 D 之前的一切订单)
SENTINEL_START = date(2000, 1, 1)


class PricingSkuVersion(Base):
    __tablename__ = "pricing_sku_version"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    product_code: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    # 已关闭区间 [period_start, period_end): 订单 order_date 落此区间 → 用本行快照值
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False, index=True)   # = 调价生效日 D

    # 成本列 (供订单成本回溯: _pricing_cost_for / wood / parts)
    physical_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    factory_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    logistics_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    install_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    wood_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    external_parts_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    packaging_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    # 售价列 (审计/参考; 订单收入用实付不用这些)
    list_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    daily_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    small_promo: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    mid_promo: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    big_promo: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))

    snapshot: Mapped[Optional[str]] = mapped_column(Text)   # 完整定价值 JSON (还原/审计用)
    note: Mapped[Optional[str]] = mapped_column(String(255))
    created_by: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_pricing_version_sku_period", "sku_code", "period_end"),
    )
