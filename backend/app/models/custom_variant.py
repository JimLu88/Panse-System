"""尺寸微定制变更记录 (业务需求 §2 / plan §3 表 3b)。

客户下普通链接但要求微定制 → 系统生成新 SKU (原 sku_code + "改NN")，
克隆 BOM 行并按尺寸调整，记一行 CustomVariant 留痕。
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class CustomVariant(Base, TimestampMixin):
    __tablename__ = "custom_variants"

    id: Mapped[int] = mapped_column(primary_key=True)
    base_sku_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    custom_sku_code: Mapped[str] = mapped_column(String(48), unique=True, nullable=False, index=True)
    related_order_no: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    product_code: Mapped[Optional[str]] = mapped_column(String(32), index=True)

    # 尺寸变更：{ "长": 1800 → 2000, "宽": 380 → 400 }
    dimension_overrides: Mapped[Optional[dict]] = mapped_column(JSON)
    # BOM 行变更：{material_code: new_qty} — 由 customization_service 计算
    bom_overrides: Mapped[Optional[dict]] = mapped_column(JSON)
    # 是否需要新建定制物料 (材料库里没有的)
    auto_created_materials: Mapped[Optional[list]] = mapped_column(JSON, default=list)

    note: Mapped[Optional[str]] = mapped_column(Text)
    created_by: Mapped[Optional[str]] = mapped_column(String(64))
