"""代付/预付台账 — 补单佣金 / 补单快递 / 售后 这三类"实际打款"的进项来源。

背景: 这三类对账此前没有"进项"(真实打款)数据源, 只有订单侧"应摊"(出项):
  - 补单佣金: RefillRecord.commission (应摊) ↔ 本表 refill_commission (实付)
  - 补单快递: RefillRecord.refill_freight (应摊) ↔ 本表 refill_express (实付)
  - 售后:     AfterSales 各项 (应摊) ↔ 本表 aftersales (实付)
每行 = 一笔实际代付/打款, 按 pay_no 去重。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import Date, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

# 台账分类
PREPAY_CATEGORIES = ("refill_commission", "refill_express", "aftersales")


class PrepayLedger(Base, TimestampMixin):
    __tablename__ = "prepay_ledgers"

    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    pay_no: Mapped[Optional[str]] = mapped_column(String(64), unique=True, index=True)  # 打款流水号(去重)
    order_no: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    pay_date: Mapped[Optional[date]] = mapped_column(Date, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"), nullable=False)  # 实际打款额
    payee: Mapped[Optional[str]] = mapped_column(String(128))  # 收款方(刷手/快递/客户/万师傅)
    source: Mapped[str] = mapped_column(String(16), default="import", nullable=False)
    remark: Mapped[Optional[str]] = mapped_column(Text)
