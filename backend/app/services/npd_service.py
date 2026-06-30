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

from app.models.npd import (
    NpdProject, NpdStage, NpdStageInstance, NpdStageTaskTemplate, NpdTask,
)

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


# 阶段待办模板 (P1): stage_code -> [(title, category, is_required)]; sort 按列表顺序。
_TASK_TEMPLATE_SEED: dict[str, list[tuple]] = {
    "S01": [("填写机会陈述(解决什么痛点)", "通用", True),
            ("设定目标价位+目标毛利率(成本门基线)", "成本", True),
            ("战略契合度自评", "通用", False)],
    "S02": [("竞品3-5款参数/价格对标", "通用", True),
            ("目标客群+使用场景", "通用", False),
            ("初步销量预估", "通用", False)],
    "S03": [("定设计边界(尺寸/主辅材/价位/品牌语言)", "设计", True),
            ("AI检索同类案例/材质建议", "设计", False)],
    "S04": [("出概念图/效果图", "设计", True), ("上传图库", "设计", False)],
    "S05": [("按评审意见修改设计", "设计", True), ("更新策划", "通用", False)],
    "S06": [("再设计定稿候选", "设计", True)],
    "S07": [("列工艺难点+工厂可行性确认", "工厂", True),
            ("登记≥2家后备供应商候选", "采购", True),
            ("AI给材质/配件工艺方法+设计边界", "设计", False)],
    "S08": [("按工程反馈修改设计", "设计", True)],
    "S09": [("设计冻结定稿", "设计", True),
            ("生成产品档案(产品+BOM+定价)", "通用", True),
            ("出BOM+线框尺寸图", "设计", False)],
    "S10": [("对每家候选发询价(AI话术)", "采购", True),
            ("收齐报价并选定供应商", "采购", True)],
    "S11": [("下采购单", "采购", True), ("跟进配件到货", "工厂", False)],
    "S12": [("工厂生产工程样首件", "工厂", True),
            ("樱桃木等易变色木材中途先做防护", "工厂", True),
            ("配件到场对照BOM核对", "工厂", False),
            ("记录打样工艺问题点", "工厂", False)],
    "S13": [("白胚逐项验收(尺寸/结构/含水率/无开裂)", "工厂", True)],
    "S14": [("返工项记录", "工厂", False), ("复验通过", "工厂", True)],
    "S15": [("外观/饰面定稿", "设计", True),
            ("送检甲醛/承重/力学并上传报告", "工厂", True),
            ("道具采买计划", "摄影", False)],
    "S17": [("整体安装验收(装好再打包)", "工厂", True)],
    "S18": [("包装方案设计", "通用", True), ("运输破损测试", "工厂", True)],
    "S16": [("产线一致性/良率/色差检查", "工厂", True)],
    "S19": [("量产排期确认", "工厂", False)],
    "S20": [("道具采买(确认清单+预算)", "摄影", True),
            ("样品搬运到摄影场地", "摄影", True),
            ("评价图拍摄", "摄影", True),
            ("评价图策划(场景/构图)", "摄影", False)],
    "S21": [("详情页摄影", "摄影", True), ("详情页拍摄策划(卖点分镜)", "摄影", False)],
    "S22": [("详情页设计排版", "摄影", True), ("文案撰写(可AI辅助)", "通用", False)],
    "S23": [("定价录入", "成本", True), ("淘宝上架", "通用", True),
            ("库存/重新入库就绪", "通用", True)],
    "S24": [("实际成本vs估算成本对比", "成本", True),
            ("销量/退货/口碑回收→反哺选品", "通用", True)],
    "G3": [("算工艺改进后量产成本对比价位靶(红绿灯)", "成本", True)],
}


def seed_task_templates(db: Session) -> int:
    """幂等种入阶段待办模板。已存在(按 stage_code+title)则跳过。"""
    existing = {
        (sc, t) for (sc, t) in db.execute(
            select(NpdStageTaskTemplate.stage_code, NpdStageTaskTemplate.title)
        ).all()
    }
    n = 0
    for stage_code, items in _TASK_TEMPLATE_SEED.items():
        for sort, (title, category, is_required) in enumerate(items):
            if (stage_code, title) in existing:
                continue
            db.add(NpdStageTaskTemplate(
                stage_code=stage_code, title=title, category=category,
                is_required=is_required, sort=sort,
            ))
            n += 1
    if n:
        db.commit()
    return n


def _instantiate_stage_tasks(db: Session, project_id: int, inst: NpdStageInstance,
                             stage_code: str, deadline) -> int:
    """按模板给某阶段实例生成任务。幂等: 该 stage_instance 已有任务则跳过。"""
    has = db.execute(
        select(NpdTask.id).where(NpdTask.stage_instance_id == inst.id).limit(1)
    ).first()
    if has:
        return 0
    tmpls = db.execute(
        select(NpdStageTaskTemplate).where(NpdStageTaskTemplate.stage_code == stage_code)
        .order_by(NpdStageTaskTemplate.sort)
    ).scalars().all()
    n = 0
    for t in tmpls:
        db.add(NpdTask(
            project_id=project_id, stage_instance_id=inst.id, stage_code=stage_code,
            template_id=t.id, title=t.title, category=t.category,
            is_required=t.is_required, status="open", due_date=deadline, sort=t.sort,
        ))
        n += 1
    return n


def undone_required_tasks(db: Session, stage_instance_id: int) -> list[str]:
    rows = db.execute(
        select(NpdTask.title).where(
            NpdTask.stage_instance_id == stage_instance_id,
            NpdTask.is_required.is_(True),
            NpdTask.status != "done",
        )
    ).scalars().all()
    return list(rows)


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


def _open_instance(db: Session, project_id: int, stage_id: int, sla_days: int) -> NpdStageInstance:
    now = datetime.now(timezone.utc)
    inst = NpdStageInstance(
        project_id=project_id, stage_id=stage_id, status="active",
        entered_at=now, deadline=now + timedelta(days=max(0, sla_days)),
    )
    db.add(inst)
    db.flush()
    return inst


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
        inst = _open_instance(db, proj.id, stage.id, stage.default_sla_days)
        _instantiate_stage_tasks(db, proj.id, inst, stage.code, inst.deadline)
    db.commit()
    db.refresh(proj)
    return proj


def _percent_for_stage(db: Session, stage_id: int) -> int:
    ids = _visible_stage_ids(db)
    if stage_id not in ids or len(ids) <= 1:
        return 0
    return int(round(ids.index(stage_id) / (len(ids) - 1) * 100))


def move_project(db: Session, project: NpdProject, target_stage_id: int,
                 *, actor: Optional[str] = None, force: bool = False) -> NpdProject:
    """流转到目标阶段: (前进时校验当前阶段必做项) → 关当前实例 → 改 current_stage_id
    → 开新实例 + 按模板生成待办 → 更新进度/终态。

    过门 (用户拍板"完成才能下一步"): 向后(sequence 增大)流转前, 当前阶段必做任务必须全完成,
    否则抛 ValueError(列出未完成项); force=True 可强制(管理员跳过)。
    """
    target = db.get(NpdStage, target_stage_id)
    if target is None:
        raise ValueError(f"阶段 {target_stage_id} 不存在")
    now = datetime.now(timezone.utc)
    cur = db.execute(
        select(NpdStageInstance).where(
            NpdStageInstance.project_id == project.id,
            NpdStageInstance.status == "active",
        ).order_by(NpdStageInstance.id.desc())
    ).scalars().first()
    cur_stage = db.get(NpdStage, project.current_stage_id) if project.current_stage_id else None

    # 过门校验: 仅"前进"时卡; 返工/回退不卡
    if (not force and cur is not None and cur_stage is not None
            and target.sequence > cur_stage.sequence):
        undone = undone_required_tasks(db, cur.id)
        if undone:
            raise ValueError(
                f"当前阶段「{cur_stage.name}」还有必做项未完成, 不能进入下一步: "
                + "、".join(undone[:8]) + ("…" if len(undone) > 8 else "")
            )

    if cur is not None and cur.stage_id != target_stage_id:
        cur.status = "done"
        cur.completed_at = now
    project.current_stage_id = target_stage_id
    project.percent_done = _percent_for_stage(db, target_stage_id)
    if target.is_final:
        project.state = "done"
    elif project.state == "done":
        project.state = "active"
    if cur is None or cur.stage_id != target_stage_id:
        inst = _open_instance(db, project.id, target_stage_id, target.default_sla_days)
        _instantiate_stage_tasks(db, project.id, inst, target.code, inst.deadline)
    db.commit()
    db.refresh(project)
    return project


def get_active_instance(db: Session, project_id: int) -> Optional[NpdStageInstance]:
    return db.execute(
        select(NpdStageInstance).where(
            NpdStageInstance.project_id == project_id,
            NpdStageInstance.status == "active",
        ).order_by(NpdStageInstance.id.desc())
    ).scalars().first()


def toggle_task(db: Session, task: NpdTask, done: bool, *, by: Optional[str] = None) -> NpdTask:
    task.status = "done" if done else "open"
    task.done_at = datetime.now(timezone.utc) if done else None
    task.done_by = by if done else None
    db.commit()
    db.refresh(task)
    return task


def project_timeline(db: Session, project: NpdProject) -> list[dict]:
    """单品详情时间线: 可见阶段 + 每阶段实例状态 + 该阶段任务。"""
    stages = list_stages(db)
    # 实例: 每 stage_id 取最新一条
    inst_by_stage: dict[int, NpdStageInstance] = {}
    for ins in db.execute(
        select(NpdStageInstance).where(NpdStageInstance.project_id == project.id)
        .order_by(NpdStageInstance.id)
    ).scalars().all():
        inst_by_stage[ins.stage_id] = ins
    tasks = db.execute(
        select(NpdTask).where(NpdTask.project_id == project.id)
        .order_by(NpdTask.sort, NpdTask.id)
    ).scalars().all()
    tasks_by_inst: dict[int, list[NpdTask]] = {}
    for t in tasks:
        tasks_by_inst.setdefault(t.stage_instance_id or 0, []).append(t)
    out: list[dict] = []
    for s in stages:
        ins = inst_by_stage.get(s.id)
        out.append({
            "stage": s,
            "instance": ins,
            "tasks": tasks_by_inst.get(ins.id, []) if ins else [],
            "is_current": project.current_stage_id == s.id,
        })
    return out
