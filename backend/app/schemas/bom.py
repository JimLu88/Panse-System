from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict


class BomLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    product_code: str
    product_name: Optional[str] = None        # BOM 清单页显示用(join 产品总表)
    product_image_url: Optional[str] = None    # 产品图片(join 产品总表)
    sku: Optional[str]
    sku_code: Optional[str]
    material_code: str
    material_name: Optional[str] = None
    unit: Optional[str]
    qty_per_product: Decimal


class BomLineUpdate(BaseModel):
    """编辑单条 BOM 行(BOM 清单页用): 改 SKU 归属 / 料号 / 单耗 / 单位等, 全部可选。"""
    product_code: Optional[str] = None
    sku: Optional[str] = None
    sku_code: Optional[str] = None
    material_code: Optional[str] = None
    material_name: Optional[str] = None
    unit: Optional[str] = None
    qty_per_product: Optional[Decimal] = None


class BomLineGroup(BaseModel):
    """按 SKU 分组的 BOM 视图。"""
    sku: Optional[str]
    sku_code: Optional[str]
    lines: list[BomLineOut]
