"""中央物流追踪 API — 实时查快递 + 列各业务实体的物流。

通用端点, 任何带单号的实体 (订单/售后/工厂单/补单/配件采购) 都用同一套:
    GET  /api/shipments?entity_type=order&entity_id=123   列该实体物流行
    POST /api/shipments/refresh?entity_type=order&entity_id=123  即时刷新该实体
    POST /api/shipments/sync                              扫全表 ensure + 刷新在途 (手动触发)
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from uuid import UUID, uuid4
from pydantic import Field
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


from app.dependencies import require_role
from app.models.fulfillment_intake import FulfillmentIntake, FulfillmentBatch
from app.services import fulfillment_batch_service as batches
from app.services.taobao_shipping import ShippingGate
from sqlalchemy import select


@router.get("/registrations")
def registrations(db: Session = Depends(get_db), _=Depends(require_role("admin", "operator"))):
    recent = db.scalars(select(FulfillmentIntake).order_by(FulfillmentIntake.created_at.desc()).limit(200))
    return [{key: getattr(row, key) for key in ("message_id", "created_at", "order_no", "sub_order_no",
              "carrier", "tracking_no", "state", "detail", "batch_id")} for row in recent]


class ShippingPreview(BaseModel):
    order_no: str = Field(min_length=10, max_length=24)
    carrier: str = Field(min_length=1, max_length=64)
    tracking_no: str = Field(min_length=6, max_length=64)


class ManualShipping(ShippingPreview):
    request_id: UUID
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    all_goods_handed_over: bool
    notes_fulfilled: bool = False


class PrepareShipping(BaseModel):
    request_id: UUID
    message_ids: list[str] = Field(min_length=1, max_length=200)


@router.post('/batch-preview')
def batch_preview(body: ShippingPreview, db: Session = Depends(get_db),
                  _=Depends(require_role('admin', 'operator'))):
    try:
        return batches.preview(db, **body.model_dump())
    except ShippingGate as exc:
        raise HTTPException(409, str(exc))


@router.post('/registrations/manual')
def register_manual(body: ManualShipping, db: Session = Depends(get_db),
                    user=Depends(require_role('admin', 'operator'))):
    values = body.model_dump()
    values['request_id'] = str(body.request_id)
    try:
        row = batches.register_manual(db, actor=f'ERP:{user.id}', **values)
        return {'message_id': row.message_id, 'state': row.state, 'detail': row.detail}
    except ShippingGate as exc:
        db.rollback()
        raise HTTPException(409, str(exc))


class StartExecution(BaseModel):
    attempt_id: UUID = Field(default_factory=uuid4)


class RecordSubmission(BaseModel):
    platform_task_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    attempt_id: UUID
    file_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class FinishExecution(BaseModel):
    platform_task_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,64}$')
    attempt_id: UUID
    file_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    task_status: str
    success_count: int = Field(ge=0, le=200)
    fail_count: int = Field(ge=0, le=200)
    order_results: dict = Field(default_factory=dict)
    raw_receipt: Optional[dict] = None


def _batch_info(row):
    return {'id': row.id, 'created_at': row.created_at, 'state': row.state,
            'count': len(row.rows), 'sha256': row.file_sha256,
            'shop': row.rows[0].get('shop') if row.rows else None,
            'platform_task_id': getattr(row, 'platform_task_id', None),
            'execution': getattr(row, 'execution', None),
            'rows': row.rows}


@router.get('/batches')
def shipping_batches(db: Session = Depends(get_db), _=Depends(require_role('admin', 'operator'))):
    return [_batch_info(r) for r in db.scalars(select(FulfillmentBatch).order_by(
        FulfillmentBatch.created_at.desc()).limit(100))]


@router.post('/batches')
def prepare_batch(body: PrepareShipping, db: Session = Depends(get_db),
                  user=Depends(require_role('admin', 'operator'))):
    try:
        row = batches.prepare(db, request_id=str(body.request_id), actor=f'ERP:{user.id}',
                              message_ids=body.message_ids)
        return _batch_info(row)
    except ShippingGate as exc:
        db.rollback()
        raise HTTPException(409, str(exc))


@router.get('/batches/{batch_id}/file')
def batch_file(batch_id: UUID, db: Session = Depends(get_db), _=Depends(require_role('admin', 'operator'))):
    import hashlib
    row = db.get(FulfillmentBatch, str(batch_id))
    if not row:
        raise HTTPException(404, '批次不存在')
    if hashlib.sha256(row.file_bytes).hexdigest() != row.file_sha256:
        raise HTTPException(409, '批次文件校验失败，不能下载')
    return Response(content=row.file_bytes,
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition': f'attachment; filename="taobao-shipping-{row.id}.xlsx"',
                 'Cache-Control': 'no-store'})


@router.get('/batches/{batch_id}')
def get_batch(batch_id: UUID, db: Session = Depends(get_db), _=Depends(require_role('admin', 'operator'))):
    row = db.get(FulfillmentBatch, str(batch_id))
    if not row:
        raise HTTPException(404, '批次不存在')
    return _batch_info(row)


@router.get('/batches/{batch_id}/precheck')
def precheck_batch(batch_id: UUID, db: Session = Depends(get_db), _=Depends(require_role('admin', 'operator'))):
    try:
        return batches.precheck_batch(db, str(batch_id))
    except ShippingGate as exc:
        raise HTTPException(409, str(exc))


@router.post('/batches/{batch_id}/execution/start')
def start_execution(batch_id: UUID, body: StartExecution, db: Session = Depends(get_db),
                    user=Depends(require_role('admin', 'operator'))):
    import os
    if os.environ.get('PANSE_BATCH_SHIPPING_ENABLED') != '1':
        raise HTTPException(409, '批量实发尚未完成平台验收，执行入口未启用')
    try:
        row = batches.record_execution_start(db, batch_id=str(batch_id), actor=f'ERP:{user.id}',
                                             attempt_id=str(body.attempt_id))
        return {**_batch_info(row), 'upload_allowed': True}
    except ShippingGate as exc:
        db.rollback()
        raise HTTPException(409, str(exc))


@router.post('/batches/{batch_id}/execution/record')
def record_submission(batch_id: UUID, body: RecordSubmission, db: Session = Depends(get_db),
                      user=Depends(require_role('admin', 'operator'))):
    try:
        row = batches.record_platform_submission(db, batch_id=str(batch_id),
                                                platform_task_id=body.platform_task_id,
                                                file_sha256=body.file_sha256,
                                                attempt_id=str(body.attempt_id), actor=f'ERP:{user.id}')
        return _batch_info(row)
    except ShippingGate as exc:
        db.rollback()
        raise HTTPException(409, str(exc))


@router.post('/batches/{batch_id}/execution/finish')
def finish_execution(batch_id: UUID, body: FinishExecution, db: Session = Depends(get_db),
                     user=Depends(require_role('admin', 'operator'))):
    try:
        row = batches.finish_batch_execution(db, batch_id=str(batch_id), actor=f'ERP:{user.id}',
                                            platform_task_id=body.platform_task_id,
                                            attempt_id=str(body.attempt_id), file_sha256=body.file_sha256,
                                            task_status=body.task_status,
                                            success_count=body.success_count,
                                            fail_count=body.fail_count,
                                            order_results=body.order_results,
                                            raw_receipt=body.raw_receipt)
        return _batch_info(row)
    except ShippingGate as exc:
        db.rollback()
        raise HTTPException(409, str(exc))


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
