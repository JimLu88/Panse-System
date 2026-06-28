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
    # 物料下料尺寸 (join 物料库 Material; 绘图子程序按 product_code 取真实下料用, 其余接口留空)
    material_width_mm: Optional[Decimal] = None
    material_height_mm: Optional[Decimal] = None
    material_area: Optional[Decimal] = None
    material_size_type: Optional[str] = None
    # AI 推演/确认尺寸 (配件 epic 阶段1)
    est_size: Optional[str] = None
    size_status: Optional[str] = None


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


class SizeReviewRow(BaseModel):
    """BOM 尺寸复核行 (配件 epic 阶段1d): AI 推演的面积料尺寸, 供人工核对/编辑。"""
    id: int
    product_code: str
    product_name: Optional[str] = None
    sku: Optional[str] = None
    material_code: str
    material_name: Optional[str] = None
    category: Optional[str] = None
    remark: Optional[str] = None       # 原备注(可能含真实尺寸; 计算时优先于 est_size)
    est_size: Optional[str] = None     # 推演/确认尺寸串 "长*深"
    size_status: Optional[str] = None  # inferred | confirmed
    area: Optional[float] = None       # 用于分摊的面积(remark 优先, 缺则 est_size)


class SizeReviewPatch(BaseModel):
    """编辑一行推演尺寸。confirm=True 时置为「已确认」(前端二次确认后)。"""
    est_size: str
    confirm: bool = False


class SizeInferRunIn(BaseModel):
    """触发尺寸推演(可指定分类; apply=False 仅预览)。"""
    categories: Optional[list[str]] = None
    apply: bool = False
    use_ai: bool = False
