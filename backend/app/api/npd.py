"""新品开发(NPD)板块 API (P0)。

GET  /api/npd/stages              阶段/门定义(按设置过滤量产组)
GET  /api/npd/projects            项目列表(含当前阶段/截止)
POST /api/npd/projects            立项
PUT  /api/npd/projects/{id}       改项目字段
PUT  /api/npd/projects/{id}/move  流转到目标阶段(看板拖拽)
GET  /api/npd/settings            前端用: 生产线开关 / 后备供应商最少家数
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.models.auth import User
from app.models.npd import NpdProject, NpdStage, NpdStageInstance, NpdTask
from app.services import npd_service

router = APIRouter(prefix="/api/npd", tags=["npd"])


# ----------------------------- 输出模型 ----------------------------- #

class StageOut(BaseModel):
    id: int
    code: str
    name: str
    group: str
    sequence: int
    color: Optional[str] = None
    is_gate: bool
    is_default: bool
    is_final: bool
    requires_mass_production: bool
    default_sla_days: int


class ProjectOut(BaseModel):
    id: int
    code: str
    name: str
    category: Optional[str] = None
    brand: Optional[str] = None
    product_line: Optional[str] = None
    current_stage_id: Optional[int] = None
    current_stage_code: Optional[str] = None
    current_stage_name: Optional[str] = None
    current_stage_group: Optional[str] = None
    state: str
    kanban_state: str
    owner: Optional[str] = None
    priority: str
    target_launch_date: Optional[date] = None
    percent_done: int
    target_price: Optional[Decimal] = None
    target_margin_rate: Optional[Decimal] = None
    product_code: Optional[str] = None
    remark: Optional[str] = None
    deadline: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


def _project_out(p: NpdProject, stages: dict[int, NpdStage],
                 deadlines: dict[int, str]) -> ProjectOut:
    st = stages.get(p.current_stage_id) if p.current_stage_id else None
    return ProjectOut(
        id=p.id, code=p.code, name=p.name, category=p.category, brand=p.brand,
        product_line=p.product_line, current_stage_id=p.current_stage_id,
        current_stage_code=(st.code if st else None),
        current_stage_name=(st.name if st else None),
        current_stage_group=(st.group if st else None),
        state=p.state, kanban_state=p.kanban_state, owner=p.owner, priority=p.priority,
        target_launch_date=p.target_launch_date, percent_done=p.percent_done,
        target_price=p.target_price, target_margin_rate=p.target_margin_rate,
        product_code=p.product_code, remark=p.remark, deadline=deadlines.get(p.id),
        created_at=p.created_at.isoformat() if p.created_at else None,
        updated_at=p.updated_at.isoformat() if p.updated_at else None,
    )


# ----------------------------- 输入模型 ----------------------------- #

class ProjectIn(BaseModel):
    name: str
    category: Optional[str] = None
    brand: Optional[str] = None
    product_line: Optional[str] = None
    owner: Optional[str] = None
    priority: str = "mid"
    target_launch_date: Optional[date] = None
    target_price: Optional[Decimal] = None
    target_margin_rate: Optional[Decimal] = None
    remark: Optional[str] = None


class ProjectPatch(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    brand: Optional[str] = None
    product_line: Optional[str] = None
    owner: Optional[str] = None
    priority: Optional[str] = None
    kanban_state: Optional[str] = None
    state: Optional[str] = None
    target_launch_date: Optional[date] = None
    target_price: Optional[Decimal] = None
    target_margin_rate: Optional[Decimal] = None
    remark: Optional[str] = None


class MoveIn(BaseModel):
    stage_id: int
    force: bool = False


class TaskToggleIn(BaseModel):
    done: bool


class TaskOut(BaseModel):
    id: int
    title: str
    category: str
    is_required: bool
    status: str
    assignee: Optional[str] = None
    stage_code: Optional[str] = None
    due_date: Optional[str] = None
    done_at: Optional[str] = None
    done_by: Optional[str] = None
    remark: Optional[str] = None


class TimelineItem(BaseModel):
    stage_id: int
    code: str
    name: str
    group: str
    is_gate: bool
    is_current: bool
    instance_status: Optional[str] = None
    entered_at: Optional[str] = None
    deadline: Optional[str] = None
    completed_at: Optional[str] = None
    tasks: list[TaskOut] = []


class ProjectDetailOut(BaseModel):
    project: ProjectOut
    timeline: list[TimelineItem]


def _task_out(t: NpdTask) -> TaskOut:
    return TaskOut(
        id=t.id, title=t.title, category=t.category, is_required=t.is_required,
        status=t.status, assignee=t.assignee, stage_code=t.stage_code,
        due_date=t.due_date.isoformat() if t.due_date else None,
        done_at=t.done_at.isoformat() if t.done_at else None,
        done_by=t.done_by, remark=t.remark,
    )


# ----------------------------- 端点 ----------------------------- #

@router.get("/stages", response_model=list[StageOut])
def list_stages(
    include_mass_production: Optional[bool] = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    stages = npd_service.list_stages(db, include_mass_production=include_mass_production)
    return [StageOut(
        id=s.id, code=s.code, name=s.name, group=s.group, sequence=s.sequence,
        color=s.color, is_gate=s.is_gate, is_default=s.is_default, is_final=s.is_final,
        requires_mass_production=s.requires_mass_production,
        default_sla_days=s.default_sla_days,
    ) for s in stages]


def _load_ctx(db: Session) -> tuple[dict[int, NpdStage], dict[int, str]]:
    stages = {s.id: s for s in db.execute(select(NpdStage)).scalars().all()}
    # 每项目当前 active 实例的截止
    deadlines: dict[int, str] = {}
    rows = db.execute(
        select(NpdStageInstance.project_id, NpdStageInstance.deadline)
        .where(NpdStageInstance.status == "active")
        .order_by(NpdStageInstance.id.desc())
    ).all()
    for pid, dl in rows:
        if pid not in deadlines and dl is not None:
            deadlines[pid] = dl.isoformat()
    return stages, deadlines


@router.get("/projects", response_model=list[ProjectOut])
def list_projects(
    state: Optional[str] = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    q = select(NpdProject).order_by(NpdProject.id.desc())
    if state:
        q = q.where(NpdProject.state == state)
    projects = db.execute(q).scalars().all()
    stages, deadlines = _load_ctx(db)
    return [_project_out(p, stages, deadlines) for p in projects]


@router.post("/projects", response_model=ProjectOut)
def create_project(
    payload: ProjectIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    if not (payload.name or "").strip():
        raise HTTPException(400, "名称必填")
    proj = npd_service.create_project(
        db, name=payload.name.strip(), category=payload.category, brand=payload.brand,
        product_line=payload.product_line, owner=payload.owner or getattr(user, "username", None),
        priority=payload.priority or "mid", target_launch_date=payload.target_launch_date,
        target_price=payload.target_price, target_margin_rate=payload.target_margin_rate,
        remark=payload.remark,
    )
    stages, deadlines = _load_ctx(db)
    return _project_out(proj, stages, deadlines)


@router.put("/projects/{project_id}", response_model=ProjectOut)
def update_project(
    project_id: int,
    payload: ProjectPatch,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    proj = db.get(NpdProject, project_id)
    if proj is None:
        raise HTTPException(404, "项目不存在")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(proj, field, value)
    db.commit()
    db.refresh(proj)
    stages, deadlines = _load_ctx(db)
    return _project_out(proj, stages, deadlines)


@router.put("/projects/{project_id}/move", response_model=ProjectOut)
def move_project(
    project_id: int,
    payload: MoveIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    proj = db.get(NpdProject, project_id)
    if proj is None:
        raise HTTPException(404, "项目不存在")
    try:
        npd_service.move_project(db, proj, payload.stage_id,
                                 actor=getattr(user, "username", None), force=payload.force)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    stages, deadlines = _load_ctx(db)
    return _project_out(proj, stages, deadlines)


@router.get("/projects/{project_id}/detail", response_model=ProjectDetailOut)
def project_detail(
    project_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    proj = db.get(NpdProject, project_id)
    if proj is None:
        raise HTTPException(404, "项目不存在")
    stages, deadlines = _load_ctx(db)
    items: list[TimelineItem] = []
    for row in npd_service.project_timeline(db, proj):
        s = row["stage"]
        ins = row["instance"]
        items.append(TimelineItem(
            stage_id=s.id, code=s.code, name=s.name, group=s.group, is_gate=s.is_gate,
            is_current=row["is_current"],
            instance_status=(ins.status if ins else None),
            entered_at=(ins.entered_at.isoformat() if ins and ins.entered_at else None),
            deadline=(ins.deadline.isoformat() if ins and ins.deadline else None),
            completed_at=(ins.completed_at.isoformat() if ins and ins.completed_at else None),
            tasks=[_task_out(t) for t in row["tasks"]],
        ))
    return ProjectDetailOut(project=_project_out(proj, stages, deadlines), timeline=items)


@router.put("/tasks/{task_id}", response_model=TaskOut)
def toggle_task(
    task_id: int,
    payload: TaskToggleIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    t = db.get(NpdTask, task_id)
    if t is None:
        raise HTTPException(404, "任务不存在")
    npd_service.toggle_task(db, t, payload.done, by=getattr(user, "username", None))
    return _task_out(t)


@router.get("/settings")
def get_settings(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    from app.services import settings_service
    raw_min = settings_service.get(db, npd_service.KEY_MIN_SUPPLIERS, env_fallback=False)
    try:
        min_suppliers = int(raw_min) if raw_min else 2
    except (TypeError, ValueError):
        min_suppliers = 2
    return {
        "mass_production_enabled": npd_service.mass_production_enabled(db),
        "min_supplier_candidates": min_suppliers,
    }
