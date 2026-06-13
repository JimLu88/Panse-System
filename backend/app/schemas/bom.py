from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict


class BomLineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    product_code: str
    product_name: Optional[str] = None        # BOM 清单页显示用(join 产品总表)
    product_image_url: Optional[str] = None    # 产品图片(join 产品总表)
    product_category: Optional[str] = None     # 产品类目(join 产品总表, 供按类目筛)
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


class BomLineCreate(BaseModel):
    """行内新增一条 BOM (图2): 选已有物料编码, 或给新物料名+前缀自动建编码。"""
    product_code: str
    sku: Optional[str] = None
    sku_code: Optional[str] = None
    material_code: Optional[str] = None        # 选已有物料编码
    new_material_name: Optional[str] = None     # 或: 新建物料的名称 (编码自动生成/或同时填 material_code)
    material_prefix: Optional[str] = "AC"        # 新建物料自动编码前缀 (AC/MP/MW/SP)
    unit: Optional[str] = "套"
    qty_per_product: Optional[Decimal] = None


class BomLineGroup(BaseModel):
    """按 SKU 分组的 BOM 视图。"""
    sku: Optional[str]
    sku_code: Optional[str]
    lines: list[BomLineOut]
