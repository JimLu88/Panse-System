"""定时任务清单 API (Phase 1A, 功能 18 "告知我多久完成一次" 的数据源).

GET  /api/scheduler/jobs                   所有注册任务 + 下次执行时间
GET  /api/scheduler/runs                   最近 N 次执行日志
POST /api/scheduler/jobs/{id}/trigger      立即执行一次 (admin)
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.models.auth import User
from app.models.scheduled_job import ScheduledJobRun
from app.services import scheduler as scheduler_service

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])


class JobInfo(BaseModel):
    job_id: str
    label: str
    kind: str
    schedule: dict
    next_run_at: Optional[str]


@router.get("/jobs", response_model=list[JobInfo])
def list_jobs(
    _: User = Depends(require_role("admin", "operator")),
):
    return [JobInfo(**j) for j in scheduler_service.list_jobs()]


class RunOut(BaseModel):
    id: int
    job_id: str
    job_label: str
    status: str
    duration_ms: Optional[int]
    error: Optional[str]
    result_summary: Optional[dict]
    started_at: Optional[str]
    completed_at: Optional[str]
    created_at: str


@router.get("/runs", response_model=list[RunOut])
def list_runs(
    limit: int = 100,
    job_id: Optional[str] = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    q = select(ScheduledJobRun).order_by(ScheduledJobRun.id.desc()).limit(limit)
    if job_id:
        q = q.where(ScheduledJobRun.job_id == job_id)
    rows = db.execute(q).scalars().all()
    return [
        RunOut(
            id=r.id, job_id=r.job_id, job_label=r.job_label,
            status=r.status, duration_ms=r.duration_ms,
            error=r.error, result_summary=r.result_summary,
            started_at=r.started_at.isoformat() if r.started_at else None,
            completed_at=r.completed_at.isoformat() if r.completed_at else None,
            created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]


@router.post("/jobs/{job_id}/trigger")
def trigger_job(
    job_id: str,
    _: User = Depends(require_role("admin")),
):
    ok = scheduler_service.trigger_now(job_id)
    if not ok:
        raise HTTPException(404, f"job {job_id} 未注册")
    return {"accepted": True}
