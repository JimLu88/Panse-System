"""审批 API (Phase 11, 完成 Tier 2 #4)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.models.auth import User
from app.services import approval_service

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


class ApprovalOut(BaseModel):
    id: int
    kind: str
    target_table: Optional[str]
    target_id: Optional[int]
    title: str
    detail: Optional[str]
    payload_json: Optional[dict]
    status: str
    requested_by: str
    approver: Optional[str]
    approved_at: Optional[str]
    reject_reason: Optional[str]
    created_at: str


def _out(r) -> ApprovalOut:
    return ApprovalOut(
        id=r.id, kind=r.kind, target_table=r.target_table, target_id=r.target_id,
        title=r.title, detail=r.detail, payload_json=r.payload_json,
        status=r.status, requested_by=r.requested_by, approver=r.approver,
        approved_at=r.approved_at.isoformat() if r.approved_at else None,
        reject_reason=r.reject_reason, created_at=r.created_at.isoformat(),
    )


class CreateApprovalIn(BaseModel):
    kind: str
    title: str
    payload: dict
    detail: Optional[str] = None


@router.post("", response_model=ApprovalOut, status_code=201)
def create(
    payload: CreateApprovalIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    r = approval_service.create_request(
        db, kind=payload.kind, title=payload.title,
        payload=payload.payload, detail=payload.detail,
        requester=getattr(user, "username", "user"),
    )
    db.commit()
    return _out(r)


@router.get("", response_model=list[ApprovalOut])
def list_all(
    status: Optional[str] = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    return [_out(r) for r in approval_service.list_requests(db, status=status, limit=limit)]


class RejectIn(BaseModel):
    reason: str


@router.post("/{request_id}/approve", response_model=ApprovalOut)
def approve(
    request_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    try:
        r = approval_service.approve(
            db, request_id, approver=getattr(user, "username", "admin"),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return _out(r)


@router.post("/{request_id}/reject", response_model=ApprovalOut)
def reject(
    request_id: int, payload: RejectIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    try:
        r = approval_service.reject(
            db, request_id, approver=getattr(user, "username", "admin"),
            reason=payload.reason,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return _out(r)
