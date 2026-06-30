"""木作工厂月结销账 (用户 2026-07-01): 供应商(博冠)月结货款 → 按月把已开账单未付的工厂单翻已付。

- FactorySettlementPayment: 一笔销账记录 (按 供应商+结算月), 触发=关键词/手动, 可撤销留痕。
- FactorySupplierAlias: 支付宝对手方/账户 → 供应商 别名 (博冠货款走个人账户 伟男/程卫燕, 流水里是打码名)。

设计要点见 [[project_panse-monthly-settlement-center]] 木作月结销账方案: 声明驱动(不卡金额)、可撤销、自愈。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import DateTime, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

DEFAULT_WOOD_SUPPLIER = "玉山县博冠家具有限公司"


class FactorySettlementPayment(Base, TimestampMixin):
    """工厂月结销账记录: 某供应商某月"已付清"声明 → 把该月已开账单未付的工厂单翻已付。

    声明驱动(不靠金额相等): trigger=keyword(支付宝备注自动识别) / manual(异常列表或月结页一键)。
    paid_amount/alipay_flow_no 仅作台账参考与审计, 不作销账门槛(工厂常有减免/加费)。
    reversed_at 非空 = 已撤销(回滚时把当时翻的单恢复 unpaid)。
    """
    __tablename__ = "factory_settlement_payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    settlement_month: Mapped[str] = mapped_column(String(7), nullable=False, index=True)  # "YYYY-MM"
    trigger: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)     # keyword / manual
    alipay_flow_no: Mapped[Optional[str]] = mapped_column(String(128))   # 关联货款流水(审计, 可空)
    paid_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))  # 实付金额(参考, 非门槛)
    flipped_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 本次翻成已付的工厂单数
    created_by: Mapped[Optional[str]] = mapped_column(String(64))
    note: Mapped[Optional[str]] = mapped_column(Text)
    # 撤销: 非空=已回滚; 回滚把本记录翻过的工厂单恢复 unpaid (按 settlement_payment_id 反查)
    reversed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    reversed_by: Mapped[Optional[str]] = mapped_column(String(64))


class FactorySupplierAlias(Base, TimestampMixin):
    """支付宝对手方/账户名 → 木作供应商 的别名映射。

    博冠货款走个人账户(伟男/程卫燕), 流水对手方常是打码名(如 **男/**英)。
    匹配采用双向包含(对手方含别名 或 别名含对手方)+ 去打码星号比对, 兼容打码。
    """
    __tablename__ = "factory_supplier_aliases"

    id: Mapped[int] = mapped_column(primary_key=True)
    supplier: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    alias: Mapped[str] = mapped_column(String(255), nullable=False)   # 对手方名/账户名/片段
    note: Mapped[Optional[str]] = mapped_column(Text)
