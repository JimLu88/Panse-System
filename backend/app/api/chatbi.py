# -*- coding: utf-8 -*-
"""ChatBI 问数 API (Plan4 v2 §6)。P0 仅 admin (数据敏感; 显式 require_role('admin'))。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.chatbi import llm_client
from app.chatbi import service as chatbi_service
from app.chatbi import templates as T
from app.database import get_db
from app.dependencies import require_role
from app.models.auth import User
from app.models.chatbi_query import ChatbiQuery

router = APIRouter(prefix="/api/chatbi", tags=["chatbi"])


class AskIn(BaseModel):
    question: str


class FeedbackIn(BaseModel):
    query_id: int
    feedback: str            # up / down
    note: Optional[str] = None


def _username(user: User) -> str:
    return getattr(user, "username", None) or getattr(user, "name", None) or f"uid:{user.id}"


@router.post("/ask")
def ask(body: AskIn, db: Session = Depends(get_db),
        user: User = Depends(require_role("admin"))) -> dict:
    question = (body.question or "").strip()
    if not question:
        return {"route": "refused", "badge": "refused", "message": "请输入问题",
                "columns": [], "rows": [], "chart": {"type": "table"}, "caliber_notes": ["请输入问题"]}
    return chatbi_service.ask(db, question, username=_username(user))


@router.get("/suggestions")
def suggestions(db: Session = Depends(get_db),
                user: User = Depends(require_role("admin"))) -> dict:
    return {"suggestions": T.suggestions(), "llm_online": llm_client.is_available(db)}


@router.get("/history")
def history(limit: int = 20, db: Session = Depends(get_db),
            user: User = Depends(require_role("admin"))) -> dict:
    rows = db.execute(
        select(ChatbiQuery).order_by(desc(ChatbiQuery.id)).limit(max(1, min(limit, 100)))
    ).scalars().all()
    return {"items": [{
        "id": x.id, "question": x.question, "route": x.route, "badge_key": x.route,
        "template_key": x.template_key, "status": x.status, "row_count": x.row_count,
        "duration_ms": x.duration_ms, "feedback": x.feedback,
        "created_at": x.created_at.isoformat() if x.created_at else None,
    } for x in rows]}


@router.post("/feedback")
def feedback(body: FeedbackIn, db: Session = Depends(get_db),
             user: User = Depends(require_role("admin"))) -> dict:
    ok = chatbi_service.set_feedback(db, body.query_id, body.feedback, body.note)
    return {"ok": ok}
