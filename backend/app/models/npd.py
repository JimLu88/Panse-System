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
