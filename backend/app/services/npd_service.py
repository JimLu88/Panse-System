"""新品开发(NPD)板块服务 (P0): 阶段 seed + 立项 + 阶段流转 + 列表。

阶段模型见 docs/新品开发板块_执行plan.md v2。量产组(requires_mass_production)默认隐藏,
受 system_settings.npd_mass_production_enabled 控制。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.npd import NpdProject, NpdStage, NpdStageInstance

# ---- 设置键 ----
KEY_MASS_PRODUCTION = "npd_mass_production_enabled"   # 生产线开关, 默认关
KEY_MIN_SUPPLIERS = "npd_min_supplier_candidates"     # 后备供应商最少家数, 默认 2

_GROUP_COLOR = {
    "plan": "#1a73e8", "design": "#7c4dff", "sourcing": "#00acc1",
    "prototype": "#fb8c00", "production": "#e53935", "launch": "#43a047",
    "review": "#757575",
}
_GATE_COLOR = "#f9a825"

# (code, name, group, sequence, is_gate, sla_days, requires_mp, allow_release, is_default, is_final)
_STAGE_SEED: list[tuple] = [
    ("S01", "立项申请", "plan", 10, False, 2, False, False, True, False),
    ("S02", "市场调研+竞品", "plan", 20, False, 3, False, False, False, False),
    ("G1", "立项门(价位靶/成本上限)", "plan", 30, True, 2, False, False, False, False),
    ("S03", "概念策划", "design", 40, False, 3, False, False, False, False),
    ("S04", "初版设计", "design", 50, False, 5, False, False, False, False),
    ("S05", "设计修改+改策划", "design", 60, False, 3, False, False, False, False),
    ("S06", "再设计", "design", 70, False, 3, False, False, False, False),
    ("S07", "工厂工程讨论(寻源前置≥2家)", "design", 80, False, 3, False, False, False, False),
    ("S08", "修改设计", "design", 90, False, 3, False, False, False, False),
    ("S09", "设计落地(自动建档)", "design", 100, False, 3, False, False, False, False),
    ("G2", "设计冻结门", "design", 110, True, 2, False, True, False, False),
    ("S10", "供应商询价", "sourcing", 120, False, 5, False, False, False, False),
    ("S11", "配件采购", "sourcing", 130, False, 7, False, False, False, False),
    ("G3", "成本预算门(硬Kill)", "sourcing", 140, True, 2, False, True, False, False),
    ("S12", "工程样EVT", "prototype", 150, False, 10, False, False, False, False),
    ("S13", "白胚验收", "prototype", 160, False, 3, False, False, False, False),
    ("S14", "修改变动/复验", "prototype", 170, False, 5, False, False, False, False),
    ("S15", "确认样DVT(送检)", "prototype", 180, False, 7, False, False, False, False),
    ("G4", "确认样+安规门", "prototype", 190, True, 2, False, True, False, False),
    ("S17", "整体安装验收", "prototype", 200, False, 3, False, False, False, False),
    ("S18", "包装设计+运输测试", "prototype", 210, False, 5, False, False, False, False),
    ("S16", "小批试产PVT", "production", 220, False, 10, True, False, False, False),
    ("S19", "量产", "production", 230, False, 15, True, False, False, False),
    ("S20", "评价图拍摄", "launch", 240, False, 5, False, False, False, False),
    ("S21", "详情页摄影", "launch", 250, False, 3, False, False, False, False),
    ("S22", "详情页设计制作", "launch", 260, False, 5, False, False, False, False),
    ("S23", "重新入库+定价+上架", "launch", 270, False, 3, False, False, False, False),
    ("G5", "上市放行门", "launch", 280, True, 2, False, True, False, False),
    ("S24", "上市后复盘", "review", 290, False, 7, False, False, False, True),
]


def seed_stages(db: Session) -> int:
    """幂等种入阶段定义。已存在(按 code)则跳过, 返回新增条数。"""
    existing = {c for (c,) in db.execute(select(NpdStage.code)).all()}
    n = 0
    for (code, name, group, seq, is_gate, sla, req_mp, allow_rel, is_def, is_fin) in _STAGE_SEED:
        if code in existing:
            continue
        db.add(NpdStage(
            code=code, name=name, group=group, sequence=seq,
            color=(_GATE_COLOR if is_gate else _GROUP_COLOR.get(group)),
            is_gate=is_gate, is_default=is_def, is_final=is_fin,
            allow_release=allow_rel, requires_mass_production=req_mp,
            default_sla_days=sla, warn_days=5, critical_days=2,
        ))
        n += 1
    if n:
        db.commit()
    return n


def mass_production_enabled(db: Session) -> bool:
    from app.services import settings_service
    raw = settings_service.get(db, KEY_MASS_PRODUCTION, env_fallback=False)
    return str(raw or "").strip().lower() in ("1", "true", "on", "yes")


def list_stages(db: Session, *, include_mass_production: Optional[bool] = None) -> list[NpdStage]:
    """按 sequence 列阶段。include_mass_production=None 时读设置决定是否含量产组。"""
    if include_mass_production is None:
        include_mass_production = mass_production_enabled(db)
    q = select(NpdStage).order_by(NpdStage.sequence)
    if not include_mass_production:
        q = q.where(NpdStage.requires_mass_production.is_(False))
    return list(db.execute(q).scalars().all())


def _visible_stage_ids(db: Session) -> list[int]:
    return [s.id for s in list_stages(db)]


def next_project_code(db: Session) -> str:
    """NPD + 4 位顺序号。"""
    n = db.execute(select(func.count(NpdProject.id))).scalar() or 0
    seq = n + 1
    while db.execute(select(NpdProject.id).where(NpdProject.code == f"NPD{seq:04d}")).first():
        seq += 1
    return f"NPD{seq:04d}"


def _default_stage(db: Session) -> Optional[NpdStage]:
    s = db.execute(select(NpdStage).where(NpdStage.is_default.is_(True))
                   .order_by(NpdStage.sequence)).scalars().first()
    if s is None:
        s = db.execute(select(NpdStage).order_by(NpdStage.sequence)).scalars().first()
    return s


def _open_instance(db: Session, project_id: int, stage_id: int, sla_days: int) -> None:
    now = datetime.now(timezone.utc)
    db.add(NpdStageInstance(
        project_id=project_id, stage_id=stage_id, status="active",
        entered_at=now, deadline=now + timedelta(days=max(0, sla_days)),
    ))


def create_project(db: Session, *, name: str, category: Optional[str] = None,
                   brand: Optional[str] = None, product_line: Optional[str] = None,
                   owner: Optional[str] = None, priority: str = "mid",
                   target_launch_date=None, target_price: Optional[Decimal] = None,
                   target_margin_rate: Optional[Decimal] = None,
                   remark: Optional[str] = None) -> NpdProject:
    """立项: 建项目 → 落到起始阶段 + 开该阶段实例。"""
    stage = _default_stage(db)
    proj = NpdProject(
        code=next_project_code(db), name=name, category=category, brand=brand,
        product_line=product_line, owner=owner, priority=priority,
        target_launch_date=target_launch_date, target_price=target_price,
        target_margin_rate=target_margin_rate, remark=remark,
        current_stage_id=(stage.id if stage else None), state="active", percent_done=0,
    )
    db.add(proj)
    db.flush()
    if stage is not None:
        _open_instance(db, proj.id, stage.id, stage.default_sla_days)
    db.commit()
    db.refresh(proj)
    return proj


def _percent_for_stage(db: Session, stage_id: int) -> int:
    ids = _visible_stage_ids(db)
    if stage_id not in ids or len(ids) <= 1:
        return 0
    return int(round(ids.index(stage_id) / (len(ids) - 1) * 100))


def move_project(db: Session, project: NpdProject, target_stage_id: int,
                 *, actor: Optional[str] = None) -> NpdProject:
    """流转到目标阶段: 关当前实例 → 改 current_stage_id → 开新实例 → 更新进度/终态。"""
    target = db.get(NpdStage, target_stage_id)
    if target is None:
        raise ValueError(f"阶段 {target_stage_id} 不存在")
    now = datetime.now(timezone.utc)
    # 关掉当前 active 实例
    cur = db.execute(
        select(NpdStageInstance).where(
            NpdStageInstance.project_id == project.id,
            NpdStageInstance.status == "active",
        ).order_by(NpdStageInstance.id.desc())
    ).scalars().first()
    if cur is not None and cur.stage_id != target_stage_id:
        cur.status = "done"
        cur.completed_at = now
    project.current_stage_id = target_stage_id
    project.percent_done = _percent_for_stage(db, target_stage_id)
    if target.is_final:
        project.state = "done"
    elif project.state == "done":
        project.state = "active"
    # 开目标阶段实例(同阶段重复点不重复开)
    if cur is None or cur.stage_id != target_stage_id:
        _open_instance(db, project.id, target_stage_id, target.default_sla_days)
    db.commit()
    db.refresh(project)
    return project
