"""定时任务清单 API (Phase 1A, 功能 18 "告知我多久完成一次" 的数据源).

GET  /api/scheduler/jobs                   所有注册任务 + 下次执行时间
GET  /api/scheduler/runs                   最近 N 次执行日志
POST /api/scheduler/jobs/{id}/trigger      立即执行一次 (admin)
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.models.auth import User
from app.models.scheduled_job import ScheduledJobRun
from app.services import scheduler as scheduler_service
from app.services import automation_failure_recorder_service

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])


class JobInfo(BaseModel):
    job_id: str
    label: str
    kind: str
    schedule: dict
    default_schedule: Optional[dict] = None
    enabled: bool = True
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


class FailureEventOut(BaseModel):
    id: int
    date: str
    category: str
    category_label: str
    job_id: str
    job_label: str
    attempt_no: int
    reason: str
    state: str
    final: bool
    waiting_input: bool
    next_retry_at: Optional[str]
    recovered_at: Optional[str]
    started_at: Optional[str]
    completed_at: Optional[str]
    duration_ms: Optional[int]
    source_failures: list[dict[str, str]]
    result_summary: dict[str, Any]


class FailureRecorderOut(BaseModel):
    date: str
    total: int
    open_count: int
    by_category: dict[str, int]
    items: list[FailureEventOut]


@router.get("/failures", response_model=FailureRecorderOut)
def list_failures(
    on: Optional[date] = None,
    category: Optional[str] = None,
    limit: int = 500,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    """Daily append-only failure recorder for critical automations."""
    try:
        return automation_failure_recorder_service.list_failure_events(
            db, on=on, category=category, limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/jobs/{job_id}/trigger")
def trigger_job(
    job_id: str,
    _: User = Depends(require_role("admin")),
):
    ok = scheduler_service.trigger_now(job_id)
    if not ok:
        raise HTTPException(404, f"job {job_id} 未注册")
    return {"accepted": True}


class ScheduleIn(BaseModel):
    interval_minutes: Optional[int] = None   # interval 任务: 间隔分钟
    cron: Optional[dict] = None              # cron 任务: {hour, minute, ...}
    enabled: Optional[bool] = None           # 启用/停用


@router.put("/jobs/{job_id}/schedule", response_model=JobInfo)
def set_job_schedule(
    job_id: str,
    payload: ScheduleIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    """用户自定义某定时任务的执行时间 / 间隔, 或启停。即时生效, 无需重启。"""
    try:
        info = scheduler_service.set_schedule(
            db, job_id,
            interval_minutes=payload.interval_minutes,
            cron=payload.cron,
            enabled=payload.enabled,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return JobInfo(**info)
