from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.models.auth import AuditLog, User

router = APIRouter(prefix="/api/audit", tags=["audit"])


class AuditLogOut(BaseModel):
    id: int
    user_id: Optional[int]
    username: Optional[str]
    method: str
    path: str
    status_code: Optional[int]
    ip: Optional[str]
    request_body: Optional[dict]
    created_at: datetime


@router.get("/logs", response_model=list[AuditLogOut])
def list_audit(
    method: Optional[str] = None,
    path_prefix: Optional[str] = None,
    limit: int = Query(100, le=500),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role("admin")),
):
    stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if method:
        stmt = stmt.where(AuditLog.method == method)
    if path_prefix:
        stmt = stmt.where(AuditLog.path.like(f"{path_prefix}%"))
    return [
        AuditLogOut(
            id=l.id, user_id=l.user_id, username=l.username, method=l.method,
            path=l.path, status_code=l.status_code, ip=l.ip,
            request_body=l.request_body, created_at=l.created_at,
        )
        for l in db.execute(stmt).scalars()
    ]
