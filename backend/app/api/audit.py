import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.models.auth import AuditLog, User

router = APIRouter(prefix="/api/audit", tags=["audit"])


def audit_retention_days() -> int:
    try:
        return int(os.environ.get("AUDIT_RETENTION_DAYS", "180"))
    except ValueError:
        return 180


def prune_audit_logs(db: Session, days: Optional[int] = None) -> int:
    """删除超过留存期的审计日志, 返回删除条数 (供定时任务 + 手动端点复用)。"""
    days = days or audit_retention_days()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    res = db.execute(delete(AuditLog).where(AuditLog.created_at < cutoff))
    db.commit()
    return res.rowcount or 0


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


@router.get("/stats")
def audit_stats(db: Session = Depends(get_db), _admin: User = Depends(require_role("admin"))):
    """审计日志统计: 总条数 / 最早一条时间 / 留存天数 (供后台留存策略展示)。"""
    total = db.execute(select(func.count()).select_from(AuditLog)).scalar() or 0
    oldest = db.execute(select(func.min(AuditLog.created_at))).scalar()
    return {
        "total": total,
        "oldest": oldest.isoformat() if oldest else None,
        "retention_days": audit_retention_days(),
    }


@router.post("/prune")
def audit_prune(
    days: Optional[int] = Query(None, description="留存天数, 不传用默认"),
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role("admin")),
):
    """手动清理超期审计日志 (定时任务也会每日自动跑)。"""
    n = prune_audit_logs(db, days)
    return {"deleted": n, "retention_days": days or audit_retention_days()}
