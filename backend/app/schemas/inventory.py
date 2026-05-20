from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PartInventoryCreate(BaseModel):
    warehouse: str = Field(..., max_length=64)
    material_code: Optional[str] = Field(None, max_length=32)
    material_name: Optional[str] = Field(None, max_length=255)
    physical_qty: int = 0
    locked_qty: int = 0
    spec: Optional[str] = None
    unit: Optional[str] = None
    remark: Optional[str] = None

    @model_validator(mode="after")
    def _at_least_one_identifier(self):
        if not self.material_code and not self.material_name:
            raise ValueError("material_code 或 material_name 至少要填一个")
        return self


class PartInventoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    warehouse: str
    material_code: str
    spec: Optional[str]
    unit: Optional[str]
    physical_qty: int
    locked_qty: int
    available_qty: int
    remark: Optional[str]


class PartInventoryAddResponse(BaseModel):
    inventory: PartInventoryOut
    material_code: str
    material_name: str
    material_created: bool
