"""中央物流追踪 API — 实时查快递 + 列各业务实体的物流。

通用端点, 任何带单号的实体 (订单/售后/工厂单/补单/配件采购) 都用同一套:
    GET  /api/shipments?entity_type=order&entity_id=123   列该实体物流行
    POST /api/shipments/refresh?entity_type=order&entity_id=123  即时刷新该实体
    POST /api/shipments/sync                              扫全表 ensure + 刷新在途 (手动触发)
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import shipment_service

router = APIRouter(prefix="/api/shipments", tags=["shipments"])


class ShipmentOut(BaseModel):
    id: int
    entity_type: str
    entity_id: int
    tracking_no: str
    carrier_name: Optional[str] = None
    provider: Optional[str] = None
    mapped_status: Optional[str] = None
    last_status: Optional[str] = None
    is_signed: bool
    active: bool
    events: Optional[list] = None
    queried_at: Optional[str] = None
    last_error: Optional[str] = None

    @classmethod
    def of(cls, s) -> "ShipmentOut":
        return cls(
            id=s.id, entity_type=s.entity_type, entity_id=s.entity_id,
            tracking_no=s.tracking_no, carrier_name=s.carrier_name, provider=s.provider,
            mapped_status=s.mapped_status, last_status=s.last_status, is_signed=s.is_signed,
            active=s.active, events=s.events,
            queried_at=s.queried_at.isoformat() if s.queried_at else None,
            last_error=s.last_error,
        )


@router.get("", response_model=list[ShipmentOut])
def list_shipments(entity_type: str, entity_id: int, db: Session = Depends(get_db)):
    return [ShipmentOut.of(s) for s in shipment_service.list_for_entity(db, entity_type, entity_id)]


@router.post("/refresh", response_model=list[ShipmentOut])
def refresh_entity(entity_type: str, entity_id: int, db: Session = Depends(get_db)):
    try:
        shipment_service.refresh_entity(db, entity_type, entity_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return [ShipmentOut.of(s) for s in shipment_service.list_for_entity(db, entity_type, entity_id)]


@router.post("/sync")
def sync_all(db: Session = Depends(get_db)):
    """扫全表 ensure + 刷新所有在途 (管理/手动触发)。"""
    return shipment_service.sync_and_refresh(db)
