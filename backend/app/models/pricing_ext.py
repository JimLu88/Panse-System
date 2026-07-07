"""定价扩展 — 配件成本拆分 + 平台活动价."""
from __future__ import annotations
from datetime import datetime
from decimal import Decimal
from typing import Optional
from sqlalchemy import JSON, DateTime, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin

class PricingSkuCosts(Base, TimestampMixin):
    __tablename__ = "pricing_sku_costs"
    id: Mapped[int] = mapped_column(primary_key=True)
    sku_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    # 22 配件成本字段
    rock_slab: Mapped[Optional[Decimal]] = mapped_column(Numeric(12,2))        # 岩板
    drawer_rail: Mapped[Optional[Decimal]] = mapped_column(Numeric(12,2))      # 抽屉轨道
    led_strip: Mapped[Optional[Decimal]] = mapped_column(Numeric(12,2))        # 灯带
    glass: Mapped[Optional[Decimal]] = mapped_column(Numeric(12,2))            # 玻璃
    electric_rail: Mapped[Optional[Decimal]] = mapped_column(Numeric(12,2))    # 电力轨道
    packing_sheet: Mapped[Optional[Decimal]] = mapped_column(Numeric(12,2))    # 打包纸片
    iron_pin: Mapped[Optional[Decimal]] = mapped_column(Numeric(12,2))         # 铁销
    connector: Mapped[Optional[Decimal]] = mapped_column(Numeric(12,2))        # 连接片
    aluminum_rail: Mapped[Optional[Decimal]] = mapped_column(Numeric(12,2))    # 铝合金轨道
    plastic_rail: Mapped[Optional[Decimal]] = mapped_column(Numeric(12,2))     # 塑料轨道
    mini_handle: Mapped[Optional[Decimal]] = mapped_column(Numeric(12,2))      # mini把手
    nail_free_glue: Mapped[Optional[Decimal]] = mapped_column(Numeric(12,2))   # 免钉胶
    engraving: Mapped[Optional[Decimal]] = mapped_column(Numeric(12,2))        # 雕刻
    acrylic_strip: Mapped[Optional[Decimal]] = mapped_column(Numeric(12,2))    # 亚克力条
    embedded_sleeve: Mapped[Optional[Decimal]] = mapped_column(Numeric(12,2))  # 预埋套杆
    cable_mgmt: Mapped[Optional[Decimal]] = mapped_column(Numeric(12,2))       # 理线架+插排
    back_panel: Mapped[Optional[Decimal]] = mapped_column(Numeric(12,2))       # 背板
    stainless_trim: Mapped[Optional[Decimal]] = mapped_column(Numeric(12,2))   # 装饰条（不锈钢）
    leg: Mapped[Optional[Decimal]] = mapped_column(Numeric(12,2))              # 腿部
    soft_pack: Mapped[Optional[Decimal]] = mapped_column(Numeric(12,2))        # 软包
    bed_board: Mapped[Optional[Decimal]] = mapped_column(Numeric(12,2))        # 床铺板
    other_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(12,2))       # 其他
    other_desc: Mapped[Optional[str]] = mapped_column(Text)                    # 外配件说明
    parts_remark: Mapped[Optional[str]] = mapped_column(Text)                  # 配件备注

    # Plan L7: 定价配件成本 ↔ BOM 漂移标记 (BOM/物料价变动后由 pricing_bom_sync_service 维护)
    bom_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    stale_reason: Mapped[Optional[str]] = mapped_column(String(255))

class PricingSkuPromo(Base, TimestampMixin):
    __tablename__ = "pricing_sku_promo"
    id: Mapped[int] = mapped_column(primary_key=True)
    sku_code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    # 淘宝
    taobao_item_id: Mapped[Optional[str]] = mapped_column(String(64))
    taobao_url: Mapped[Optional[str]] = mapped_column(String(512))  # 淘宝链接
    taobao_sku_id: Mapped[Optional[str]] = mapped_column(String(64))
    # 一码多SKU: 同一商家编码在淘宝挂的其它 SKUID(主=taobao_sku_id)。导出报名/单品立减时, 主+每个alt 各出一行同价。
    alt_taobao_sku_ids: Mapped[Optional[list]] = mapped_column(JSON, default=list)
    taobao_activity_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12,2))  # = daily_price
    # 店内活动
    shop_promo_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(10,6))         # 单品立减系数
    shop_internal_promo: Mapped[Optional[Decimal]] = mapped_column(Numeric(12,2))     # 单品立减设置
    shop_internal_final: Mapped[Optional[Decimal]] = mapped_column(Numeric(12,2))     # 到手价=小促
    # 无国补中促 (user inputs: mid_shop_rate; rest computed)
    mid_platform_discount: Mapped[Optional[Decimal]] = mapped_column(Numeric(10,6))  # 平台立减 12%
    mid_shop_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(10,6))
    mid_buyer_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12,2))
    mid_vip_commission: Mapped[Optional[Decimal]] = mapped_column(Numeric(12,2))     # 88VIP佣金
    mid_shop_receipt: Mapped[Optional[Decimal]] = mapped_column(Numeric(12,2))
    mid_vip_final: Mapped[Optional[Decimal]] = mapped_column(Numeric(12,2))
    # 无国补大促 (user inputs: big_shop_rate; rest computed)
    big_platform_discount: Mapped[Optional[Decimal]] = mapped_column(Numeric(10,6))  # 平台立减 12%
    big_shop_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(10,6))
    big_buyer_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12,2))
    big_vip_commission: Mapped[Optional[Decimal]] = mapped_column(Numeric(12,2))     # 88VIP佣金
    big_shop_receipt: Mapped[Optional[Decimal]] = mapped_column(Numeric(12,2))
    big_vip_final: Mapped[Optional[Decimal]] = mapped_column(Numeric(12,2))
    # 小红书 (xhs)
    xhs_item_id: Mapped[Optional[str]] = mapped_column(String(64))
    xhs_sku_name: Mapped[Optional[str]] = mapped_column(String(255))
    xhs_sku_id: Mapped[Optional[str]] = mapped_column(String(64))
    xhs_list_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12,2))          # = daily_price
    xhs_activity_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12,2))      # RN单品宝报名价
    xhs_promo_discount: Mapped[Optional[Decimal]] = mapped_column(Numeric(10,6))      # = 0.15 default
    xhs_promo_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12,2))         # = xhs_activity × (1-0.15)
