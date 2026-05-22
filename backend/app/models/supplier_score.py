"""供应商月度评分快照 (Phase 6, 借鉴 Tesla 供应商管理).

每月 1 号自动算上月数据:
    on_time_rate     = 按时送达数 / 总送达数
    return_rate      = 退货数 / 总送达数
    price_variance   = 平均单价 vs 上月
    score            = 综合分 0-100

让 "选供应商靠数据"。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from sqlalchemy import JSON, Integer, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class SupplierScore(Base, TimestampMixin):
    __tablename__ = "supplier_scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)

    on_time_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    return_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    price_variance_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 2))
    total_orders: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    rank: Mapped[Optional[int]] = mapped_column(Integer)
    detail_json: Mapped[Optional[dict]] = mapped_column(JSON)

    __table_args__ = (
        UniqueConstraint("supplier_id", "year", "month", name="uq_supplier_scores_sym"),
    )
