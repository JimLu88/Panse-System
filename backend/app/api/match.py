from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import match_service

router = APIRouter(prefix="/api/match", tags=["match"])


class MatchCandidateOut(BaseModel):
    scope: str
    code: str
    name: str
    score: float


@router.get("", response_model=list[MatchCandidateOut])
def fuzzy_match(
    q: str = Query(..., min_length=1),
    scope: Literal["product", "material", "sku"] = "material",
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
):
    results = match_service.fuzzy(db, q, scope=scope, limit=limit)
    return [MatchCandidateOut(scope=r.scope, code=r.code, name=r.name, score=r.score) for r in results]
