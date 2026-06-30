"""新品开发(NPD)板块模型 (产品 Tab 下 /npd, 执行 plan v2 §8)。

P0 三张核心表:
- npd_stages          阶段/门定义(可配, seed 24阶段+5门; 量产组 requires_mass_production 默认隐藏)
- npd_projects        新品开发单(立项 → 流经各阶段 → 落地绑 product_code)
- npd_stage_instances 项目×阶段实例(记录进入/截止/完成 + 门结果, 给时间线与提醒打底)

P1/P2 的 task/inspection/supplier_candidate/cost_gate/craft_issue/knowledge 表后续迁移再加。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class NpdStage(Base):
    """阶段/门定义。code=S01..S24 / G1..G5; group=plan/design/sourcing/prototype/production/launch/review。"""
    __tablename__ = "npd_stages"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(8), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    group: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0, index=True)
    color: Mapped[Optional[str]] = mapped_column(String(16))
    is_gate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)   # 立项起始阶段
    is_final: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    allow_release: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # 过此门才允许下游(采购/上架)
    # 量产组标记: npd_mass_production_enabled=false 时这些阶段不实例化、看板不显示(用户 2026-06-30)
    requires_mass_production: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    default_sla_days: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    warn_days: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    critical_days: Mapped[int] = mapped_column(Integer, nullable=False, default=2)


class NpdProject(Base, TimestampMixin):
    """新品开发单(立项)。"""
    __tablename__ = "npd_projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    brand: Mapped[Optional[str]] = mapped_column(String(32))
    product_line: Mapped[Optional[str]] = mapped_column(String(64), index=True)   # 看板泳道
    current_stage_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("npd_stages.id", ondelete="SET NULL"), index=True
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="active", index=True)  # draft/active/rework/done/cancelled
    kanban_state: Mapped[str] = mapped_column(String(16), nullable=False, default="normal")  # normal/blocked/ready
    owner: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    priority: Mapped[str] = mapped_column(String(8), nullable=False, default="mid")  # high/mid/low
    target_launch_date: Mapped[Optional[date]] = mapped_column(Date)
    percent_done: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 成本门基线(立项时填): 目标价位 + 目标毛利率
    target_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    target_margin_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4))  # 0.0000~1
    product_code: Mapped[Optional[str]] = mapped_column(String(32), index=True)   # 落地后绑 Product
    remark: Mapped[Optional[str]] = mapped_column(Text)


class NpdStageInstance(Base, TimestampMixin):
    """项目×阶段 实例: 进入/截止/完成 + 门结果。"""
    __tablename__ = "npd_stage_instances"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("npd_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage_id: Mapped[int] = mapped_column(
        ForeignKey("npd_stages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")  # pending/active/done/skipped/rework
    entered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deadline: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    alert_level: Mapped[Optional[str]] = mapped_column(String(8))   # critical/warn
    alert_reason: Mapped[Optional[str]] = mapped_column(String(255))
    gate_result: Mapped[Optional[str]] = mapped_column(String(8))   # go/kill/hold/rework (仅门阶段)
    gate_decided_by: Mapped[Optional[str]] = mapped_column(String(64))
    gate_comment: Mapped[Optional[str]] = mapped_column(Text)


class NpdStageTaskTemplate(Base):
    """阶段待办模板: 项目进入某阶段时按此 instantiate 出一组任务 (P1)。"""
    __tablename__ = "npd_stage_task_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    stage_code: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False, default="通用")  # 通用/设计/工厂/采购/摄影/成本
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # 必做→影响过门
    offset_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class NpdTask(Base, TimestampMixin):
    """项目阶段下的待办任务实例 (P1)。"""
    __tablename__ = "npd_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("npd_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage_instance_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("npd_stage_instances.id", ondelete="CASCADE"), index=True
    )
    stage_code: Mapped[Optional[str]] = mapped_column(String(8), index=True)
    template_id: Mapped[Optional[int]] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False, default="通用")
    assignee: Mapped[Optional[str]] = mapped_column(String(64))
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")  # open/done
    due_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    done_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    done_by: Mapped[Optional[str]] = mapped_column(String(64))
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    remark: Mapped[Optional[str]] = mapped_column(Text)


class NpdInspectionTemplate(Base):
    """验收检验项模板 (P1b, 借 ERPNext Quality): 挂在打样/验收阶段。
    check_type: pass=勾选通过 / numeric=填实测值(可设 min/max 自动判) / text=填期望值匹配。"""
    __tablename__ = "npd_inspection_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    stage_code: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    item_name: Mapped[str] = mapped_column(String(128), nullable=False)
    check_type: Mapped[str] = mapped_column(String(16), nullable=False, default="pass")
    unit: Mapped[Optional[str]] = mapped_column(String(16))
    min_val: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 3))
    max_val: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 3))
    expected: Mapped[Optional[str]] = mapped_column(String(64))
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class NpdInspectionItem(Base, TimestampMixin):
    """项目阶段下的验收项实例: 填实测/勾选 → 自动或人工判 result。"""
    __tablename__ = "npd_inspection_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("npd_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage_instance_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("npd_stage_instances.id", ondelete="CASCADE"), index=True
    )
    stage_code: Mapped[Optional[str]] = mapped_column(String(8), index=True)
    template_id: Mapped[Optional[int]] = mapped_column(Integer)
    item_name: Mapped[str] = mapped_column(String(128), nullable=False)
    check_type: Mapped[str] = mapped_column(String(16), nullable=False, default="pass")
    unit: Mapped[Optional[str]] = mapped_column(String(16))
    min_val: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 3))
    max_val: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 3))
    expected: Mapped[Optional[str]] = mapped_column(String(64))
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    reading: Mapped[Optional[str]] = mapped_column(String(128))   # 实测值/勾选记录
    result: Mapped[str] = mapped_column(String(8), nullable=False, default="pending")  # pass/fail/pending
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    remark: Mapped[Optional[str]] = mapped_column(Text)


class NpdCostGate(Base, TimestampMixin):
    """成本预算门 G3 (P1c, 治"打样OK却量产巨亏"): 量产成本(含工艺改进上浮) vs 价位靶 → 红绿灯。
    每项目一条(按 project_id upsert)。"""
    __tablename__ = "npd_cost_gates"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("npd_projects.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    prototype_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))   # 打样件成本
    est_mass_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))    # 量产估算成本(含工艺上浮)
    target_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))     # 价位靶(快照自项目)
    target_margin: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4))     # 目标毛利率
    actual_margin: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 4))     # 实算毛利率(可负)
    verdict: Mapped[str] = mapped_column(String(8), nullable=False, default="pending")  # pass/fail/pending
    decided_by: Mapped[Optional[str]] = mapped_column(String(64))
    note: Mapped[Optional[str]] = mapped_column(Text)


class NpdCraftIssue(Base, TimestampMixin):
    """打样工艺问题台账 (P1c): 供应商承诺OK但打样出状况→改工艺→成本上浮; 记成本影响, 多供应商排查。"""
    __tablename__ = "npd_craft_issues"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("npd_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage_code: Mapped[Optional[str]] = mapped_column(String(8))
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    root_cause: Mapped[Optional[str]] = mapped_column(Text)
    cost_impact: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))   # 成本上浮金额
    status: Mapped[str] = mapped_column(String(8), nullable=False, default="open")  # open/solved
    chosen_supplier: Mapped[Optional[str]] = mapped_column(String(128))


class NpdSupplierCandidate(Base, TimestampMixin):
    """供应商候选 (P1c): 寻源前置≥N家 + 多供应商对齐工艺(后备可能低价解决工艺问题)。"""
    __tablename__ = "npd_supplier_candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("npd_projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    material_category: Mapped[Optional[str]] = mapped_column(String(64))
    supplier_name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_backup: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    quote_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    quote_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")  # pending/quoted/chosen
    lead_time_days: Mapped[Optional[int]] = mapped_column(Integer)
    can_solve_craft_issue: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    craft_solution: Mapped[Optional[str]] = mapped_column(Text)
    solved_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    remark: Mapped[Optional[str]] = mapped_column(Text)
