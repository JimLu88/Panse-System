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
    NpdInspectionTemplate, NpdInspectionItem,
    NpdCostGate, NpdCraftIssue, NpdSupplierCandidate, NpdBomLine, NpdKnowledgeNote,
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


# ---- 验收检验项模板 (P1b): stage_code -> [(item_name, check_type, unit, is_required)] ----
_INSPECTION_SEED: dict[str, list[tuple]] = {
    # 白胚验收: 尺寸/含水率/结构, 上漆前最后能改的节点
    "S13": [
        ("长", "numeric", "mm", True), ("宽", "numeric", "mm", True),
        ("高", "numeric", "mm", True), ("对角线差(验方正)", "numeric", "mm", True),
        ("板厚", "numeric", "mm", False), ("五金孔距/孔位", "numeric", "mm", False),
        ("含水率", "numeric", "%", True),
        ("结构/榫卯牢固", "pass", None, True),
        ("拼板平整无高低差", "pass", None, True),
        ("无开裂/虫眼/腐朽/大节疤", "pass", None, True),
        ("板面无补土过度/无砂痕", "pass", None, False),
        ("樱桃木等易变色木材已做防氧化防护", "pass", None, True),
    ],
    # 确认样 DVT: 外观/饰面定稿
    "S15": [
        ("表面无划痕/磕碰", "pass", None, True),
        ("无气泡/流挂/橘皮(漆面)", "pass", None, True),
        ("封边平整无翘起/无开胶", "pass", None, True),
        ("颜色对标准色卡无明显色差", "pass", None, True),
        ("木纹/拼缝对称美观、缝隙均匀", "pass", None, False),
        ("五金外露件无瑕疵/无锈", "pass", None, True),
        ("无补土痕/砂痕", "pass", None, False),
        ("整体观感(对称/比例/手感)合格", "pass", None, True),
        ("贴皮诚实标注+封边无翘/无气泡", "pass", None, False),
        ("玻璃钢化3C标识/磨边倒角", "pass", None, False),
        ("岩板无暗裂/崩边/平整", "pass", None, False),
    ],
    # 整体安装验收: 全部装好再打包
    "S17": [
        ("全部安装后整体试装通过", "pass", None, True),
        ("整体外观/缝隙均匀", "pass", None, True),
        ("五金开合顺滑/承重正常", "pass", None, False),
        ("配件齐全对照BOM", "pass", None, True),
        ("装好再打包+标配件+附安装说明", "pass", None, True),
    ],
}


def seed_inspection_templates(db: Session) -> int:
    """幂等种入验收模板。已存在(按 stage_code+item_name)则跳过。"""
    existing = {
        (sc, n) for (sc, n) in db.execute(
            select(NpdInspectionTemplate.stage_code, NpdInspectionTemplate.item_name)
        ).all()
    }
    n = 0
    for stage_code, items in _INSPECTION_SEED.items():
        for sort, (item_name, check_type, unit, is_required) in enumerate(items):
            if (stage_code, item_name) in existing:
                continue
            db.add(NpdInspectionTemplate(
                stage_code=stage_code, item_name=item_name, check_type=check_type,
                unit=unit, is_required=is_required, sort=sort,
            ))
            n += 1
    if n:
        db.commit()
    return n


def _instantiate_stage_inspections(db: Session, project_id: int,
                                   inst: NpdStageInstance, stage_code: str) -> int:
    """按验收模板给某阶段实例生成检验项。幂等。"""
    has = db.execute(
        select(NpdInspectionItem.id).where(NpdInspectionItem.stage_instance_id == inst.id).limit(1)
    ).first()
    if has:
        return 0
    tmpls = db.execute(
        select(NpdInspectionTemplate).where(NpdInspectionTemplate.stage_code == stage_code)
        .order_by(NpdInspectionTemplate.sort)
    ).scalars().all()
    n = 0
    for t in tmpls:
        db.add(NpdInspectionItem(
            project_id=project_id, stage_instance_id=inst.id, stage_code=stage_code,
            template_id=t.id, item_name=t.item_name, check_type=t.check_type, unit=t.unit,
            min_val=t.min_val, max_val=t.max_val, expected=t.expected,
            is_required=t.is_required, result="pending", sort=t.sort,
        ))
        n += 1
    return n


def undone_required_inspections(db: Session, stage_instance_id: int) -> list[str]:
    """必检项中尚未 pass 的(pending 或 fail 都算未过)。"""
    rows = db.execute(
        select(NpdInspectionItem.item_name).where(
            NpdInspectionItem.stage_instance_id == stage_instance_id,
            NpdInspectionItem.is_required.is_(True),
            NpdInspectionItem.result != "pass",
        )
    ).scalars().all()
    return list(rows)


def _judge_inspection(item: NpdInspectionItem, reading: Optional[str],
                      result: Optional[str]) -> str:
    """数值项有 min/max 且读数可解析 → 自动判; 否则用传入 result(勾选/人工)。"""
    if item.check_type == "numeric" and reading not in (None, "") \
            and (item.min_val is not None or item.max_val is not None):
        try:
            v = Decimal(str(reading))
        except (ArithmeticError, ValueError, TypeError):
            return result or "pending"
        lo = item.min_val if item.min_val is not None else v
        hi = item.max_val if item.max_val is not None else v
        return "pass" if lo <= v <= hi else "fail"
    return result or item.result or "pending"


def save_inspection_item(db: Session, item: NpdInspectionItem, *,
                         reading: Optional[str] = None, result: Optional[str] = None,
                         min_val: Optional[Decimal] = None, max_val: Optional[Decimal] = None,
                         remark: Optional[str] = None) -> NpdInspectionItem:
    if reading is not None:
        item.reading = reading
    if min_val is not None:
        item.min_val = min_val
    if max_val is not None:
        item.max_val = max_val
    if remark is not None:
        item.remark = remark
    item.result = _judge_inspection(item, item.reading, result)
    db.commit()
    db.refresh(item)
    return item


# ----------------------------- 成本门 G3 (P1c) ----------------------------- #

def get_cost_gate(db: Session, project_id: int) -> Optional[NpdCostGate]:
    return db.execute(
        select(NpdCostGate).where(NpdCostGate.project_id == project_id)
    ).scalars().first()


def _sum_open_craft_cost(db: Session, project_id: int) -> Decimal:
    rows = db.execute(
        select(NpdCraftIssue.cost_impact).where(
            NpdCraftIssue.project_id == project_id, NpdCraftIssue.status == "open")
    ).scalars().all()
    return sum((c for c in rows if c is not None), Decimal("0"))


def save_cost_gate(db: Session, project: NpdProject, *, prototype_cost: Optional[Decimal] = None,
                   est_mass_cost: Optional[Decimal] = None, note: Optional[str] = None,
                   decided_by: Optional[str] = None) -> NpdCostGate:
    """量产成本 vs 价位靶 → verdict。量产成本未填则 = 打样成本 + Σ未解决工艺问题成本上浮。"""
    g = get_cost_gate(db, project.id)
    if g is None:
        g = NpdCostGate(project_id=project.id)
        db.add(g)
    if prototype_cost is not None:
        g.prototype_cost = prototype_cost
    if est_mass_cost is not None:
        g.est_mass_cost = est_mass_cost
    if note is not None:
        g.note = note
    if decided_by is not None:
        g.decided_by = decided_by
    g.target_price = project.target_price
    g.target_margin = project.target_margin_rate
    est = g.est_mass_cost
    if est is None and g.prototype_cost is not None:
        est = g.prototype_cost + _sum_open_craft_cost(db, project.id)
        g.est_mass_cost = est
    if est is not None and project.target_price and project.target_price > 0:
        margin = (project.target_price - est) / project.target_price
        g.actual_margin = margin
        target_m = project.target_margin_rate if project.target_margin_rate is not None else Decimal("0")
        g.verdict = "pass" if margin >= target_m else "fail"
    else:
        g.actual_margin = None
        g.verdict = "pending"
    db.commit()
    db.refresh(g)
    return g


def cost_gate_passed(db: Session, project_id: int) -> bool:
    g = get_cost_gate(db, project_id)
    return bool(g and g.verdict == "pass")


# ----------------------------- 工艺问题台账 / 供应商候选 (P1c) ----------------------------- #

def list_craft_issues(db: Session, project_id: int) -> list[NpdCraftIssue]:
    return list(db.execute(
        select(NpdCraftIssue).where(NpdCraftIssue.project_id == project_id)
        .order_by(NpdCraftIssue.id)
    ).scalars().all())


def add_craft_issue(db: Session, project_id: int, **kw) -> NpdCraftIssue:
    obj = NpdCraftIssue(project_id=project_id, **kw)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def list_suppliers(db: Session, project_id: int) -> list[NpdSupplierCandidate]:
    return list(db.execute(
        select(NpdSupplierCandidate).where(NpdSupplierCandidate.project_id == project_id)
        .order_by(NpdSupplierCandidate.id)
    ).scalars().all())


def add_supplier(db: Session, project_id: int, **kw) -> NpdSupplierCandidate:
    obj = NpdSupplierCandidate(project_id=project_id, **kw)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def update_obj(db: Session, obj, patch: dict):
    for k, v in patch.items():
        if hasattr(obj, k):
            setattr(obj, k, v)
    db.commit()
    db.refresh(obj)
    return obj


# ----------------------------- 设计 BOM + 自动建档 (P2a) ----------------------------- #

def list_bom_lines(db: Session, project_id: int) -> list[NpdBomLine]:
    return list(db.execute(
        select(NpdBomLine).where(NpdBomLine.project_id == project_id)
        .order_by(NpdBomLine.id)
    ).scalars().all())


def add_bom_line(db: Session, project_id: int, **kw) -> NpdBomLine:
    obj = NpdBomLine(project_id=project_id, **kw)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def delete_bom_line(db: Session, line: NpdBomLine) -> None:
    db.delete(line)
    db.commit()


def materialize_project(db: Session, project: NpdProject, *, brand: str,
                        category_code: str, actor: Optional[str] = None) -> dict:
    """设计落地自动建档 (用户拍板 #3): 新配件按询价价建 Material → 建 Product+BomLine
    → draft PricingSku(physical_cost=ΣBOM, 价=价位靶, recompute) → 绑 product_code。

    复用 material_coder/product_coder/pricing_calc_service 现成口径, 不另造。幂等保护: 已建档则拒。
    """
    from app.models.bom import BomLine
    from app.models.material import Material
    from app.models.pricing import PricingSku
    from app.models.product import Product
    from app.services import material_coder, pricing_calc_service, product_coder

    if project.product_code:
        raise ValueError(f"该项目已生成产品档案 {project.product_code}, 勿重复生成")
    lines = list_bom_lines(db, project.id)
    if not lines:
        raise ValueError("请先录入设计 BOM 再生成产品档案")
    if not brand or len(brand.strip()) != 2 or not brand.strip().isalpha():
        raise ValueError("请提供 2 位字母品牌码")
    if not category_code or not category_code.strip().isdigit() or len(category_code.strip()) != 2:
        raise ValueError("请提供 2 位数字类目码")
    brand = brand.strip().upper()
    category_code = category_code.strip()

    # 1) 解析/新建物料 (新配件按 name 查重, 无则建)
    resolved: dict[int, str] = {}
    materials_created = 0
    for ln in lines:
        if ln.material_code:
            m = db.execute(select(Material).where(Material.code == ln.material_code)).scalars().first()
            if not m:
                raise ValueError(f"BOM 行物料编码 {ln.material_code} 在物料库不存在")
            resolved[ln.id] = m.code
            continue
        name = (ln.material_name or "").strip()
        if not name:
            raise ValueError("BOM 行既无编码也无名称, 无法建档")
        existing = db.execute(select(Material).where(Material.name == name)).scalars().first()
        if existing:
            resolved[ln.id] = existing.code
            continue
        prefix = "MW" if (ln.category and "木" in ln.category) else "AC"
        code = material_coder.next_code(db, prefix)
        db.add(Material(code=code, name=name, price=ln.unit_price,
                        category=ln.category, unit=ln.unit, size_type=ln.size_type))
        db.flush()
        resolved[ln.id] = code
        materials_created += 1

    # 2) 建产品
    pcode = product_coder.next_product_code(db, brand=brand, category=category_code)
    db.add(Product(code=pcode, name=project.name, brand=brand,
                   category=project.category or category_code, remark=project.remark,
                   alt_taobao_ids=[]))
    db.flush()

    # 3) BOM 行 + 物理成本累计
    phys = Decimal("0")
    for ln in lines:
        mcode = resolved[ln.id]
        m = db.execute(select(Material).where(Material.code == mcode)).scalars().first()
        qty = ln.qty or Decimal("1")
        db.add(BomLine(product_code=pcode, product_name=project.name, material_code=mcode,
                       material_name=(m.name if m else ln.material_name), unit=ln.unit,
                       qty_per_product=qty, size_type=ln.size_type, remark=ln.remark))
        if m and m.price is not None:
            phys += m.price * qty

    # 4) draft 定价表 (=定位表): 成本来自 BOM, 价取价位靶, recompute 派生
    sku = PricingSku(product_code=pcode, sku_code=f"{pcode}11",
                     physical_cost=(phys if phys > 0 else None),
                     daily_price=project.target_price, big_promo=project.target_price)
    pricing_calc_service.recompute(sku)
    db.add(sku)

    # 5) 绑定
    project.product_code = pcode
    db.commit()
    db.refresh(project)
    return {
        "product_code": pcode, "sku_code": f"{pcode}11",
        "materials_created": materials_created, "bom_lines": len(lines),
        "physical_cost": str(phys),
    }


# ----------------------------- AI 设计知识库 (P2b) ----------------------------- #

def list_knowledge_notes(db: Session, *, q: Optional[str] = None,
                         category: Optional[str] = None) -> list[NpdKnowledgeNote]:
    from sqlalchemy import or_
    query = select(NpdKnowledgeNote).order_by(NpdKnowledgeNote.id.desc())
    conds = []
    if q:
        like = f"%{q}%"
        conds.append(or_(NpdKnowledgeNote.title.ilike(like),
                         NpdKnowledgeNote.body.ilike(like),
                         NpdKnowledgeNote.material.ilike(like)))
    if category:
        conds.append(NpdKnowledgeNote.category == category)
    if conds:
        query = query.where(*conds)
    return list(db.execute(query.limit(50)).scalars().all())


def add_knowledge_note(db: Session, **kw) -> NpdKnowledgeNote:
    obj = NpdKnowledgeNote(**kw)
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


def ai_design_suggest(db: Session, *, question: str, category: Optional[str] = None,
                      material: Optional[str] = None) -> dict:
    """检索本库(知识库笔记/物料/品类工艺) → 喂 AI 给设计/材质/工艺/询价建议。
    护栏: 区分'本库已有'vs'AI推断', AI 不可用则只回检索结果, 绝不编造检测/价格/合规。"""
    from sqlalchemy import or_
    from app.models.material import Material

    sources: list[dict] = []
    ctx_parts: list[str] = []
    for n in list_knowledge_notes(db, q=(material or category)):
        if len(sources) >= 5:
            break
        sources.append({"type": "note", "name": n.title, "detail": (n.body or "")[:120]})
        ctx_parts.append(f"[知识库] {n.title}: {(n.body or '')[:300]}")
    conds = []
    if category:
        conds.append(Material.category == category)
    if material:
        conds.append(Material.name.ilike(f"%{material}%"))
    if conds:
        for m in db.execute(select(Material).where(or_(*conds)).limit(8)).scalars().all():
            sources.append({"type": "material", "name": m.name,
                            "detail": f"{m.category or ''} 单价{m.price}"})
            ctx_parts.append(f"[物料] {m.name} 分类{m.category or '-'} 单价{m.price} 单位{m.unit or '-'}")
    try:
        from app.services import custom_board_template as cbt
        prof = getattr(cbt, "CATEGORY_PROFILE", {})
        if category and isinstance(prof, dict):
            for k, v in prof.items():
                if category in str(k) or str(k) in str(category):
                    ctx_parts.append(f"[品类工艺/几何] {k}: {str(v)[:200]}")
                    sources.append({"type": "profile", "name": str(k), "detail": str(v)[:120]})
                    break
    except Exception:  # noqa: BLE001
        pass

    ctx = "\n".join(ctx_parts)
    if ctx:
        extra = ("以下是本系统已有的相关数据(物料库/知识库/品类工艺), 回答时优先据此, "
                 "明确区分「本库已有」与「通用推断」; 不确定就说不确定; "
                 "绝不编造检测数值/价格/合规结论。\n" + ctx)
    else:
        extra = ("回答家具设计/材质/工艺/询价问题。明确区分事实与推断; 不确定就说; "
                 "绝不编造检测数值/价格/合规结论。")

    suggestion = None
    ai_available = False
    try:
        from app.services import ai_assistant
        resp = ai_assistant._call_ai(db, kind="npd", user_message=question,
                                     extra_system=extra, max_tokens=900)
        suggestion = getattr(resp, "text", None)
        ai_available = bool(suggestion)
    except Exception:  # noqa: BLE001 - AI 不可用不报错, 降级给检索
        ai_available = False

    return {
        "suggestion": suggestion, "ai_available": ai_available, "sources": sources,
        "note": None if ai_available else "AI 暂不可用, 仅返回本库检索结果(可去 后台→AI 配置 接入)",
    }


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
        _instantiate_stage_inspections(db, proj.id, inst, stage.code)
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
        undone_insp = undone_required_inspections(db, cur.id)
        if undone or undone_insp:
            parts = []
            if undone:
                parts.append("待办未完成: " + "、".join(undone[:6]) + ("…" if len(undone) > 6 else ""))
            if undone_insp:
                parts.append("验收未通过: " + "、".join(undone_insp[:6]) + ("…" if len(undone_insp) > 6 else ""))
            raise ValueError(
                f"当前阶段「{cur_stage.name}」还有必做项, 不能进入下一步 —— " + "; ".join(parts)
            )
        if cur_stage.code == "G3" and not cost_gate_passed(db, project.id):
            raise ValueError(
                "成本门 G3 未通过: 量产成本未算 / 超价位靶(低于目标毛利)。"
                "请在成本门填量产成本, 或扩大供应商池/启用后备降本(不建议靠加价)。"
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
        _instantiate_stage_inspections(db, project.id, inst, target.code)
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
    insp = db.execute(
        select(NpdInspectionItem).where(NpdInspectionItem.project_id == project.id)
        .order_by(NpdInspectionItem.sort, NpdInspectionItem.id)
    ).scalars().all()
    insp_by_inst: dict[int, list[NpdInspectionItem]] = {}
    for it in insp:
        insp_by_inst.setdefault(it.stage_instance_id or 0, []).append(it)
    out: list[dict] = []
    for s in stages:
        ins = inst_by_stage.get(s.id)
        out.append({
            "stage": s,
            "instance": ins,
            "tasks": tasks_by_inst.get(ins.id, []) if ins else [],
            "inspections": insp_by_inst.get(ins.id, []) if ins else [],
            "is_current": project.current_stage_id == s.id,
        })
    return out
