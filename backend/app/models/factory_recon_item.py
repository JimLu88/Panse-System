"""工厂逐单对账明细 — 来自工厂侧对账单 xlsx (每行=一笔工厂结算)。

「价格」列 = 工厂结算价 = 我们付给工厂的成本 (用户确认口径)。
用法: 导入这份明细 → 逐月汇总「应付(Σ结算价)」↔「实付(支付宝 factory_payment 流水)」对账;
对不上的月份报异常, 可在条目上「填原因做平」(settle_reason + resolved), 这同时是手工差异归因的雏形。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class FactoryReconItem(Base, TimestampMixin):
    __tablename__ = "factory_recon_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_sheet: Mapped[Optional[str]] = mapped_column(String(64))   # 来源 sheet (26年1月 / 26年 对账单)
    doc_no: Mapped[Optional[str]] = mapped_column(String(32))         # 单号 (工厂内部流水, 会重复)
    order_no: Mapped[Optional[str]] = mapped_column(String(64), index=True)  # 订单号 (平台单号)
    extra_order_no1: Mapped[Optional[str]] = mapped_column(String(64))  # 追加订单号1
    extra_order_no2: Mapped[Optional[str]] = mapped_column(String(64))  # 追加订单号2
    detail: Mapped[Optional[str]] = mapped_column(Text)              # 详情 (型号/规格)
    qty: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    settle_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"), nullable=False)  # 价格=工厂结算价(成本)
    customer_info: Mapped[Optional[str]] = mapped_column(String(128))
    order_date: Mapped[Optional[date]] = mapped_column(Date, index=True)   # 下单时间
    ship_date: Mapped[Optional[date]] = mapped_column(Date)                # 发货时间
    remark: Mapped[Optional[str]] = mapped_column(Text)

    # 做平 / 归因 (对不上时填原因做平 — 也是手工差异归因的雏形)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    settle_reason: Mapped[Optional[str]] = mapped_column(Text)       # 扣减/减免/差异原因
    resolved_by: Mapped[Optional[str]] = mapped_column(String(64))
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    source: Mapped[str] = mapped_column(String(16), default="import", nullable=False)
