"""活动报名价 (Plan F1) — 各渠道大促报名价存档, 与定价表渠道价自动对照。

来源: Excel/CSV 导入 + 报名结果截图 OCR (用户拍板, 不做纯手工录入表单)。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import Date, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class CampaignSignupPrice(Base, TimestampMixin):
    __tablename__ = "campaign_signup_prices"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="taobao")  # taobao / xhs
    campaign_name: Mapped[Optional[str]] = mapped_column(String(128))   # 活动名 (如 618 / 双11)
    signup_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="import")  # import / ocr
    effective_date: Mapped[Optional[date]] = mapped_column(Date)
    remark: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("sku_code", "channel", "campaign_name",
                         name="uq_campaign_signup_sku_channel_campaign"),
    )
