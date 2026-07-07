"""定价总表 (Excel 表 2-定价总表 → plan §3 表 2)。

按 SKU 维度记录定价快照：4 档售价 + 成本拆分 + 大小类型。
冷启动从 Excel 导入；轻定制走 light_lookup() 直接读这表的 4 档价；
高定走 high_calc() 用 size_category + cost 即时算。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, Index, Numeric, String, Text
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
    # 按 SKU 的成品尺寸 (2026-06-19: 从 SKU 尺寸图读出回填; 下单图按订单 sku_code 取此, 多规格不再选错)
    size_info: Mapped[Optional[str]] = mapped_column(String(255))

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
    # 工厂成本手动覆盖: True=用户在定价表手改过工厂成本(不再按 木作+包装+外配件 自动派生, 保住手改值);
    # False=自动派生。物理成本恒 = 工厂成本+物流+安装。改于 2026-07-01: 治"改工厂成本不联动重算"。
    factory_cost_override: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # 成本加成基数 (2026-07-01 用户拍板对齐 Excel 定价法, 迁移0110): 各档价 = ROUNDUP(会计基准 ÷ 基数, −1),
    # 会计基准 = 物理成本 ÷ (1 − 平台税率2.6%)。基数逐 SKU/逐档不同 (来自用户 Excel List 表公式:
    # 标价基数≈0.4, 小促/中促/大促基数按 SKU 手定, 毛利率≈1−基数)。**仅"已对齐 Excel"的 SKU 填**,
    # 其余留空(None) → recompute 不走 cost-plus 推导, 保持"大促当输入"原口径(不破坏未对齐 SKU/既有测试)。
    base_list: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 4))    # 标价基数 (Excel 通常 0.4)
    base_small: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 4))   # 小促基数
    base_mid: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 4))     # 中促基数
    base_big: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 4))     # 大促基数

    image_url: Mapped[Optional[str]] = mapped_column(String(512))
    remark: Mapped[Optional[str]] = mapped_column(Text)  # 备注
    # 定制占位符 (2026-07-07): 淘宝上的"微定制/材质定制/尺寸定制/差价/追加配件"等占位链接SKU。
    # 仅用于淘宝活动报名(导出活动价=现价×0.9); 不参与产品成本/利润/对账计算; 下单走现有定制计价流程
    # (编码尾号≥90 本就被 sku_utils.is_custom_sku_code 判为定制)。recompute 对其直接跳过。
    is_custom_placeholder: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (
        Index("ix_pricing_sku_size", "size_category"),
    )
