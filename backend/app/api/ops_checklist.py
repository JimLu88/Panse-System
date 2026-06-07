"""运营待办台账 API (每日/每周/每月 例行工作清单 + 完成状态)。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.auth import User
from app.services import ops_checklist_service

router = APIRouter(prefix="/api/ops-checklist", tags=["ops-checklist"])


@router.get("")
def get_checklist(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return ops_checklist_service.status(db)


class ToggleIn(BaseModel):
    task_key: str
    done: bool


@router.post("/toggle")
def toggle_task(
    body: ToggleIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        result = ops_checklist_service.toggle(
            db, body.task_key, body.done, actor=getattr(user, "username", None),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return result
