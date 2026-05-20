from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.exception import DataException
from app.schemas.exception import DataExceptionOut, DataExceptionResolve

router = APIRouter(prefix="/api/exceptions", tags=["exceptions"])


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
