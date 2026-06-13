from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ProductCreate(BaseModel):
    name: str = Field(..., max_length=255)
    brand: str = Field(..., min_length=2, max_length=2, description="2 字母品牌码，如 PS / FG")
    category: str = Field(..., min_length=2, max_length=2, description="2 位类目码，如 33")
    category_label: Optional[str] = Field(None, description="可读类目名，如 卧室-床")
    remark: Optional[str] = None
    created_on: Optional[date] = None
    taobao_id: Optional[str] = None
    alt_taobao_ids: Optional[list[str]] = None


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    sub_name: Optional[str] = None
    brand: Optional[str] = None
    category: Optional[str] = None
    priority: Optional[str] = None
    remark: Optional[str] = None
    taobao_id: Optional[str] = None
    image_url: Optional[str] = None
    custom_scope: Optional[str] = None
    size_detail: Optional[str] = None
    size_value: Optional[str] = None
    main_material: Optional[str] = None       # 主材 (图4)
    aux_material: Optional[str] = None
    accessory_desc: Optional[str] = None
    accessory_remark: Optional[str] = None
    listing_status: Optional[str] = None
    description: Optional[str] = None


class TaobaoIdsUpdate(BaseModel):
    primary: Optional[str] = None
    alternatives: list[str] = Field(default_factory=list, max_length=5)


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    brand: Optional[str]
    category: Optional[str]
    remark: Optional[str]
    taobao_id: Optional[str] = None
    alt_taobao_ids: Optional[list[str]] = None
    image_url: Optional[str] = None
    # 图库主图缩略 URL (列表显示图库优先, 不落库; list_products 批量注入)
    gallery_image_url: Optional[str] = None
    custom_scope: Optional[str] = None
    size_detail: Optional[str] = None
    aux_material: Optional[str] = None
    description: Optional[str] = None
    # 图4: 产品编辑弹窗预填用
    sub_name: Optional[str] = None
    priority: Optional[str] = None
    size_value: Optional[str] = None
    main_material: Optional[str] = None
    accessory_desc: Optional[str] = None
    accessory_remark: Optional[str] = None
    listing_status: Optional[str] = None
