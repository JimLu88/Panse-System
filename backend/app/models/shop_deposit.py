"""店铺/平台保证金条目 — 多店铺各记各的, 手动新增/删除。

合计自动并入 cash_flow_service 的可用资金加项, 替代原来单常量 settings(cashflow_shop_deposit)。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from sqlalchemy import Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ShopDeposit(Base, TimestampMixin):
    __tablename__ = "shop_deposits"

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[Optional[str]] = mapped_column(String(64))    # 平台 (淘宝/抖音/拼多多…)
    shop_name: Mapped[str] = mapped_column(String(128), nullable=False)  # 店铺名
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"), nullable=False)  # 保证金额
    remark: Mapped[Optional[str]] = mapped_column(Text)
