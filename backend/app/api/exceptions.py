from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.exception import DataException
from app.schemas.exception import DataExceptionOut, DataExceptionResolve
from app.services import data_quality_service, exception_fix_service

router = APIRouter(prefix="/api/exceptions", tags=["exceptions"])


class FixPayload(BaseModel):
    fields: dict[str, Any]


@router.get("", response_model=list[DataExceptionOut])
def list_exceptions(
    status: Optional[str] = Query(None),
    source_table: Optional[str] = None,
    exception_type: Optional[str] = None,
    limit: int = Query(200, le=1000),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    stmt = select(DataException)
    if status:
        stmt = stmt.where(DataException.status == status)
    if source_table:
        stmt = stmt.where(DataException.source_table == source_table)
    if exception_type:
        stmt = stmt.where(DataException.exception_type == exception_type)
    stmt = stmt.order_by(DataException.id.desc()).limit(limit).offset(offset)
    return db.execute(stmt).scalars().all()


@router.patch("/{exception_id}/resolve", response_model=DataExceptionOut)
def resolve_exception(exception_id: int, payload: DataExceptionResolve, db: Session = Depends(get_db)):
    if payload.status not in {"resolved", "ignored"}:
        raise HTTPException(400, "status must be resolved or ignored")
    exc = db.get(DataException, exception_id)
    if not exc:
        raise HTTPException(404, "exception not found")
    exc.status = payload.status
    exc.resolved_by = payload.resolved_by
    exc.resolved_at = datetime.now(timezone.utc).isoformat()
    db.commit()
    db.refresh(exc)
    return exc


@router.post("/{exception_id}/fix", response_model=DataExceptionOut)
def fix_exception(exception_id: int, payload: FixPayload, db: Session = Depends(get_db)):
    """内联补填: 写回源表字段并解除异常."""
    try:
        exc = exception_fix_service.fix_exception(db, exception_id, payload.fields)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return exc


@router.post("/run-data-quality", response_model=dict)
def run_data_quality(db: Session = Depends(get_db)):
    """触发全部数据完整性扫描, 返回各规则发现数."""
    results = data_quality_service.run_all(db)
    return results


@router.get("/counts-by-type", response_model=dict)
def counts_by_type(
    status: str = Query("open"),
    db: Session = Depends(get_db),
):
    """每种 exception_type 的待处理数, 供顶栏健康度角标和对账页使用."""
    rows = (
        db.query(DataException.exception_type, func.count(DataException.id))
        .filter(DataException.status == status)
        .group_by(DataException.exception_type)
        .all()
    )
    return {r[0]: r[1] for r in rows}


@router.get("/open-count", response_model=dict)
def open_count(db: Session = Depends(get_db)):
    """顶栏健康度角标用: 返回 {count: N}."""
    n = db.query(func.count(DataException.id)).filter(DataException.status == "open").scalar()
    return {"count": n or 0}
