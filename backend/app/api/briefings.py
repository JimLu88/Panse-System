"""每日经营简报 + 供应商评分 API (Phase 8 Tier 1)."""
from __future__ import annotations

from datetime import date as _date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.models.auth import User
from app.services import briefing_service, supplier_score_service

router = APIRouter(prefix="/api/briefings", tags=["briefings"])


class BriefingOut(BaseModel):
    id: int
    for_date: str
    content: str
    highlights_json: Optional[list]
    model: Optional[str]
    generated_at: Optional[str]


@router.get("/today", response_model=Optional[BriefingOut])
def get_today(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    """业务: 顶部 banner / 首页显示最新一份简报."""
    rows = briefing_service.list_recent(db, limit=1)
    if not rows:
        return None
    b = rows[0]
    return BriefingOut(
        id=b.id, for_date=b.for_date.isoformat(), content=b.content,
        highlights_json=b.highlights_json, model=b.model,
        generated_at=b.generated_at.isoformat() if b.generated_at else None,
    )


@router.get("/recent", response_model=list[BriefingOut])
def list_recent(
    limit: int = 14,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    return [
        BriefingOut(
            id=b.id, for_date=b.for_date.isoformat(), content=b.content,
            highlights_json=b.highlights_json, model=b.model,
            generated_at=b.generated_at.isoformat() if b.generated_at else None,
        )
        for b in briefing_service.list_recent(db, limit=limit)
    ]


@router.post("/generate-now")
def trigger(
    for_date: Optional[str] = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    """admin 手动触发: 生成 (重新生成) 某日的简报."""
    target = None
    if for_date:
        try:
            target = datetime.strptime(for_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(400, "for_date 必须 YYYY-MM-DD")
    b = briefing_service.generate(db, target, push=False)
    db.commit()
    return {"for_date": b.for_date.isoformat(), "content": b.content}


# ----------------------------- 供应商评分 ---------------------------- #

supplier_router = APIRouter(prefix="/api/supplier-scores", tags=["supplier-scores"])


class SupplierScoreOut(BaseModel):
    supplier_id: int
    year: int
    month: int
    on_time_rate: Optional[float]
    return_rate: Optional[float]
    price_variance_pct: Optional[float]
    total_orders: int
    total_amount: Optional[float]
    score: Optional[float]
    rank: Optional[int]
    detail_json: Optional[dict]


@supplier_router.get("/{year}/{month}", response_model=list[SupplierScoreOut])
def get_scores(
    year: int, month: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    rows = supplier_score_service.list_for_month(db, year, month)
    return [
        SupplierScoreOut(
            supplier_id=s.supplier_id, year=s.year, month=s.month,
            on_time_rate=float(s.on_time_rate) if s.on_time_rate else None,
            return_rate=float(s.return_rate) if s.return_rate else None,
            price_variance_pct=float(s.price_variance_pct) if s.price_variance_pct else None,
            total_orders=s.total_orders,
            total_amount=float(s.total_amount) if s.total_amount else None,
            score=float(s.score) if s.score else None,
            rank=s.rank, detail_json=s.detail_json,
        )
        for s in rows
    ]


@supplier_router.post("/compute/{year}/{month}")
def compute(
    year: int, month: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    rows = supplier_score_service.compute_for_month(db, year, month)
    db.commit()
    return {"computed": len(rows), "year": year, "month": month}
