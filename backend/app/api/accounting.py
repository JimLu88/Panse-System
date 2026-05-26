"""会计期间 API (Phase 8, Tier 1 #3)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.models.auth import User
from app.services import accounting_period_service

router = APIRouter(prefix="/api/accounting", tags=["accounting"])


class PeriodOut(BaseModel):
    id: int
    year: int
    month: int
    status: str
    closed_at: Optional[str]
    closed_by: Optional[str]
    remark: Optional[str]


def _out(p) -> PeriodOut:
    return PeriodOut(
        id=p.id, year=p.year, month=p.month, status=p.status,
        closed_at=p.closed_at.isoformat() if p.closed_at else None,
        closed_by=p.closed_by, remark=p.remark,
    )


@router.get("/periods", response_model=list[PeriodOut])
def list_periods(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    return [_out(p) for p in accounting_period_service.list_periods(db)]


class PeriodActionIn(BaseModel):
    year: int
    month: int


@router.post("/periods/close", response_model=PeriodOut)
def close_period(
    payload: PeriodActionIn, db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    try:
        p = accounting_period_service.close_period(
            db, payload.year, payload.month,
            actor=getattr(user, "username", "admin"),
        )
    except accounting_period_service.PeriodLocked as e:
        raise HTTPException(400, str(e))
    db.commit()
    return _out(p)


@router.post("/periods/reopen", response_model=PeriodOut)
def reopen_period(
    payload: PeriodActionIn, db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    try:
        p = accounting_period_service.reopen_period(
            db, payload.year, payload.month,
            actor=getattr(user, "username", "admin"),
        )
    except accounting_period_service.PeriodLocked as e:
        raise HTTPException(400, str(e))
    db.commit()
    return _out(p)


@router.post("/periods/lock", response_model=PeriodOut)
def lock_period(
    payload: PeriodActionIn, db: Session = Depends(get_db),
    user: User = Depends(require_role("admin")),
):
    p = accounting_period_service.lock_period(
        db, payload.year, payload.month,
        actor=getattr(user, "username", "admin"),
    )
    db.commit()
    return _out(p)
