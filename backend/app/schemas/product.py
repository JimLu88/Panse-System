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


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    remark: Optional[str] = None


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    name: str
    brand: Optional[str]
    category: Optional[str]
    remark: Optional[str]
