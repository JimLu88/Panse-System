"""淘宝商品导出对应表 (Task 5).

从淘宝「商品导出」Excel 导入, 每行一个 SKU, 建立
商品ID / 淘宝skuId / 商家编码 / 宝贝标题 与系统内部 sku_code / product_code 的对应关系。
商家编码 (merchant_code) 是与系统对接的桥梁: 导入时尝试匹配 PricingSku.sku_code。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from sqlalchemy import Index, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class TaobaoListing(Base, TimestampMixin):
    __tablename__ = "taobao_listings"

    id: Mapped[int] = mapped_column(primary_key=True)

    taobao_item_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # 商品Id
    taobao_sku_id: Mapped[Optional[str]] = mapped_column(String(32), index=True)         # skuId
    title: Mapped[Optional[str]] = mapped_column(String(512))                            # 宝贝标题
    merchant_code: Mapped[Optional[str]] = mapped_column(String(64), index=True)         # 商家编码
    sku_spec: Mapped[Optional[str]] = mapped_column(String(255))                         # 属性对/销售属性
    category_name: Mapped[Optional[str]] = mapped_column(String(255))                    # 类目名称

    list_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))   # 一口价
    sku_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))    # SKU 价格
    stock: Mapped[Optional[int]] = mapped_column()                          # 库存(件)

    # 与系统内部的关联 (导入时按 merchant_code 自动匹配, 也可人工修正)
    sku_code: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    product_code: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    matched: Mapped[bool] = mapped_column(default=False, nullable=False)
    # 店铺 (该导出来自哪个店: 畔色店 / 孚格店) — 分店统计用 (migration 0052)
    shop: Mapped[Optional[str]] = mapped_column(String(32), index=True)

    __table_args__ = (
        UniqueConstraint("taobao_item_id", "taobao_sku_id", name="uq_taobao_item_sku"),
        Index("ix_taobao_listings_merchant_match", "merchant_code", "matched"),
    )
