"""结算账单明细 (billDetail) — 微信/聚合 + 支付宝企业号 的逐笔结算, 用于订单逐笔对账。

每行 = 一笔结算动作 (交易收款 / 扣款 / 保证金扣款 / 转账…), 按 支付流水号 去重。
income(收入) − expense(支出/扣款) 即该笔净额; 按 order_no 汇总可得该单实际到账。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import DateTime, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class OrderSettlement(Base, TimestampMixin):
    __tablename__ = "order_settlements"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(16), default="wechat", nullable=False)  # wechat(聚合) / alipay
    pay_no: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)  # 支付流水号
    order_no: Mapped[Optional[str]] = mapped_column(String(64), index=True)  # 淘宝订单编号
    settle_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))  # 入账时间
    entry_type: Mapped[Optional[str]] = mapped_column(String(32))  # 入账类型: 交易收款/扣款/保证金扣款/转账
    income: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False)   # 收入金额
    expense: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0, nullable=False)  # 支出金额(扣款)
    description: Mapped[Optional[str]] = mapped_column(Text)  # 业务描述 (软件服务费/消费券代付…)
    remark: Mapped[Optional[str]] = mapped_column(Text)
