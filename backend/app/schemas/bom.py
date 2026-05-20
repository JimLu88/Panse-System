from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict


class BomLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    product_code: str
    sku: Optional[str]
    sku_code: Optional[str]
    material_code: str
    material_name: Optional[str] = None
    unit: Optional[str]
    qty_per_product: Decimal


class BomLineGroup(BaseModel):
    """按 SKU 分组的 BOM 视图。"""
    sku: Optional[str]
    sku_code: Optional[str]
    lines: list[BomLineOut]
