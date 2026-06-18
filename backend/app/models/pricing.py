"""定价总表 (Excel 表 2-定价总表 → plan §3 表 2)。

按 SKU 维度记录定价快照：4 档售价 + 成本拆分 + 大小类型。
冷启动从 Excel 导入；轻定制走 light_lookup() 直接读这表的 4 档价；
高定走 high_calc() 用 size_category + cost 即时算。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from sqlalchemy import Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class PricingSku(Base, TimestampMixin):
    __tablename__ = "pricing_sku"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    product_name: Mapped[Optional[str]] = mapped_column(String(255))  # 产品名称
    # 淘宝宝贝标题 (宝贝级, 同产品所有 SKU 共用): 订单导入只带这个长标题、不带编码时,
    # 按 order.product_name == taobao_title 精确匹配回填 product_code → 走定价表算成本而非百分比。
    taobao_title: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    sku: Mapped[Optional[str]] = mapped_column(String(255))
    sku_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    size_category: Mapped[Optional[str]] = mapped_column(String(16))  # 小型 / 中型 / 大型

    # 四档售价 (轻定制)
    list_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))     # 标价计算
    daily_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))    # 日常价/单品宝
    small_promo: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))    # 小促 (超级立减)
    mid_promo: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))      # 中促 (88券)
    big_promo: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))      # 大促 (双11)

    # 成本拆分
    big_promo_margin: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))   # 大促利润
    gross_margin_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))  # 即时毛利率
    accounting_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))    # 会计总成本
    platform_fee_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 6))
    tax: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    physical_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))   # 物理总成本
    logistics_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    install_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    factory_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))    # 总出厂成本
    wood_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))       # 木作成本
    packaging_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    external_parts_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))

    image_url: Mapped[Optional[str]] = mapped_column(String(512))
    remark: Mapped[Optional[str]] = mapped_column(Text)  # 备注

    __table_args__ = (
        Index("ix_pricing_sku_size", "size_category"),
    )
