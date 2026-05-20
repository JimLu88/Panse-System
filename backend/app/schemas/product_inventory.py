from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class ProductInventoryCreate(BaseModel):
    warehouse: str = Field(..., max_length=64)
    product_code: str = Field(..., max_length=32)
    sku: Optional[str] = None
    spec: Optional[str] = None
    unit: Optional[str] = "个"
    physical_qty: int = 0
    locked_qty: int = 0
    remark: Optional[str] = None


class ProductInventoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    warehouse: str
    product_code: str
    sku: Optional[str]
    spec: Optional[str]
    unit: Optional[str]
    physical_qty: int
    locked_qty: int
    remark: Optional[str]
