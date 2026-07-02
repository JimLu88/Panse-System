from typing import Optional

from sqlalchemy import JSON, Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Product(Base, TimestampMixin):
    """产品总表 (Excel 表 1-产品总表) — Phase 1 占位骨架，最小字段集。"""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    sub_name: Mapped[Optional[str]] = mapped_column(String(255))           # 副名称 (从名称拆分竖线后)
    brand: Mapped[Optional[str]] = mapped_column(String(32))
    category: Mapped[Optional[str]] = mapped_column(String(64))
    remark: Mapped[Optional[str]] = mapped_column(String(255))

    # 淘宝商品 ID (主) + 备选 ID 列表 (因链接会换, 业务需求 §4)
    taobao_id: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    alt_taobao_ids: Mapped[Optional[list]] = mapped_column(JSON, default=list)

    # 重要程度 (Phase 4 库存预警 / 滞销分级用): high / mid / low
    priority: Mapped[str] = mapped_column(String(8), default="mid", nullable=False)

    # 主数据中心字段 (Phase 13)
    image_url: Mapped[Optional[str]] = mapped_column(String(512))
    custom_scope: Mapped[Optional[str]] = mapped_column(String(2000))  # 定制范围
    size_detail: Mapped[Optional[str]] = mapped_column(String(2000))   # 尺寸明细
    aux_material: Mapped[Optional[str]] = mapped_column(String(2000))  # 辅材介绍
    description: Mapped[Optional[str]] = mapped_column(String(2000))   # 产品文案

    # 导入扩展字段
    listing_status: Mapped[Optional[str]] = mapped_column(String(32))          # 上架状态
    main_material: Mapped[Optional[str]] = mapped_column(String(500))          # 主材介绍
    taobao_sku_id: Mapped[Optional[str]] = mapped_column(String(64))           # 淘宝 SKU ID
    accessory_desc: Mapped[Optional[str]] = mapped_column(String(500))         # 外配件说明
    accessory_remark: Mapped[Optional[str]] = mapped_column(String(500))       # 配件备注
    size_value: Mapped[Optional[str]] = mapped_column(String(64))              # 尺寸值 (mm)
    size_confirmed: Mapped[Optional[str]] = mapped_column(String(32))          # 尺寸是否确定
    sku: Mapped[Optional[str]] = mapped_column(String(255))                    # SKU 描述 (产品主表级)
    sku_code: Mapped[Optional[str]] = mapped_column(String(32), index=True)    # SKU 编码 (产品主表级)

    # R5 半成品/白坯 (默认全 False, 功能开关 enable_semi_finished 打开后才用):
    #   semi_finished_eligible = 该产品可用白坯前段生产 (前段共用、个性化靠后)
    #   semi_group = 共享同一白坯的分组码 (同组产品的成品预测归集算白坯备货量, 池化省安全库存)
    semi_finished_eligible: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False)
    semi_group: Mapped[Optional[str]] = mapped_column(String(64), index=True)
