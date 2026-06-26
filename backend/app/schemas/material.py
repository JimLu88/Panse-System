from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class MaterialBase(BaseModel):
    code: str = Field(..., max_length=32)
    name: str = Field(..., max_length=255)
    size_type: Optional[str] = None
    unit: Optional[str] = None
    price: Optional[Decimal] = None
    remark: Optional[str] = None
    is_custom: bool = False
    category: Optional[str] = Field(None, max_length=64)


class MaterialCreate(BaseModel):
    name: str = Field(..., max_length=255)
    code: Optional[str] = Field(None, max_length=32)
    prefix: str = Field("AC", description="code prefix when code not provided: AC/MP/MW/SP")
    size_type: Optional[str] = None
    unit: Optional[str] = None
    price: Optional[Decimal] = None
    remark: Optional[str] = None
    category: Optional[str] = Field(None, max_length=64)


class MaterialUpdate(BaseModel):
    name: Optional[str] = None
    size_type: Optional[str] = None
    unit: Optional[str] = None
    price: Optional[Decimal] = None
    remark: Optional[str] = None
    category: Optional[str] = Field(None, max_length=64)


class MaterialOut(MaterialBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
