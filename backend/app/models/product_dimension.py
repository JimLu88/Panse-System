from __future__ import annotations

from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ProductDimensionAsset(Base, TimestampMixin):
    """产品尺寸矢量资产。

    SVG/预览图本体放在群晖 ``storage/product_dimensions``，数据库只保存稳定映射、
    结构化尺寸与版本号。一个产品可以有多张视图（例如基础柜的 AA 柱/洞洞板）。
    """

    __tablename__ = "product_dimension_assets"
    __table_args__ = (
        UniqueConstraint("product_code", "asset_key", name="uq_product_dimension_product_asset"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_code: Mapped[str] = mapped_column(
        String(32), ForeignKey("products.code", ondelete="CASCADE"), nullable=False, index=True
    )
    asset_key: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    source_psd: Mapped[Optional[str]] = mapped_column(String(255))

    svg_relpath: Mapped[str] = mapped_column(String(512), nullable=False)
    preview_relpath: Mapped[Optional[str]] = mapped_column(String(512))
    metadata_relpath: Mapped[Optional[str]] = mapped_column(String(512))

    dimension_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    erp_dimensions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    sku_variants: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    mapping_status: Mapped[str] = mapped_column(
        String(32), default="confirmed", server_default="confirmed", nullable=False
    )
    match_confidence: Mapped[Optional[str]] = mapped_column(String(32))
    is_primary: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    updated_by: Mapped[Optional[str]] = mapped_column(String(128))
