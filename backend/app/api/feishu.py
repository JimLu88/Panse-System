from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.feishu_sync import FeishuTableBinding
from app.services import feishu_sync_service

router = APIRouter(prefix="/api/feishu", tags=["feishu"])


class BindingIn(BaseModel):
    system_table: str
    feishu_app_token: str
    feishu_table_id: str
    direction: str = "bidirectional"
    field_mapping: Optional[str] = None
    enabled: bool = False


class BindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    system_table: str
    feishu_app_token: str
    feishu_table_id: str
    direction: str
    enabled: bool
    field_mapping: Optional[str]


class StatusOut(BaseModel):
    system_table: str
    feishu_table_id: str
    direction: str
    enabled: bool
    mapped_rows: int


@router.get("/bindings", response_model=list[BindingOut])
def list_bindings(db: Session = Depends(get_db)):
    return db.execute(select(FeishuTableBinding).order_by(FeishuTableBinding.system_table)).scalars().all()


@router.post("/bindings", response_model=BindingOut, status_code=201)
def create_binding(payload: BindingIn, db: Session = Depends(get_db)):
    existing = db.execute(
        select(FeishuTableBinding).where(FeishuTableBinding.system_table == payload.system_table)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(409, f"binding for system_table {payload.system_table} already exists")
    b = FeishuTableBinding(**payload.model_dump())
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


@router.get("/status", response_model=list[StatusOut])
def get_status(db: Session = Depends(get_db)):
    return [
        StatusOut(**s.__dict__) for s in feishu_sync_service.list_status(db)
    ]
