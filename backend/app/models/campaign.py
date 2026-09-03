"""活动生命周期系统 (2026-07-17 spec: docs/活动生命周期系统_执行plan.md §五)。

三张表:
- CampaignPlan        活动计划 (类型/档期精确到秒/千牛标题/状态机)
- CampaignReconReport 核对报告 (每次核对一行: 汇总 + 逐SKU JSON)
- CampaignCalendar    活动日历 (每日发现的千牛活动, 距开始<3天推飞书, 同活动一天只提醒一次)
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from decimal import Decimal

from sqlalchemy import (Boolean, JSON, Date, DateTime, Integer, LargeBinary,
                        Numeric, String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

# 状态机 (spec §五): draft→precheck→discount_pushed→signup_pushed→reconciled→alarmed
CAMPAIGN_PLAN_STATUSES = (
    "draft", "precheck", "discount_pushed", "resume_executing",
    "signup_pushed", "reconciled", "alarmed",
)


class CampaignPlan(Base, TimestampMixin):
    __tablename__ = "campaign_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # 活动类型 (spec §四.3 点选按钮): super_reduce / big88 / big38 / big_other / big618 / big11
    campaign_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # 档位 (由类型派生固化): mid=超级立减10%→中促到手 / big=12%→大促到手 / big618=15%→大促到手
    tier: Mapped[str] = mapped_column(String(16), nullable=False, default="big")
    # 档期起止, 精确到秒 (spec §四.4 手动点选; 推单品立减时填千牛活动时间)
    start_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    end_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    # 千牛侧活动标题 (核对器进活动页头部校验用, 不一致立即中止+报警, spec §四.6)
    qn_campaign_title: Mapped[Optional[str]] = mapped_column(String(255))
    # 价保采用“方案3加强版”：规则未核实时默认19天；运营提供活动价保说明链接后可逐场修改。
    price_protection_days: Mapped[int] = mapped_column(Integer, nullable=False, default=19)
    price_protection_rule_url: Mapped[Optional[str]] = mapped_column(String(1024))
    price_protection_confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False, index=True)
    remark: Mapped[Optional[str]] = mapped_column(Text)
    # Formal internal-workflow identity.  Browser pages are never the ERP data source.
    workflow_key: Mapped[Optional[str]] = mapped_column(String(128), unique=True, index=True)
    # fixed_window = exact platform selling window; long_running_update = ERP update window.
    platform_activity_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default="fixed_window")
    platform_campaign_id: Mapped[Optional[str]] = mapped_column(String(64))
    platform_united_activity_id: Mapped[Optional[str]] = mapped_column(String(64))
    # Exact enrolled-record identity used only for read-only campaign exports.
    platform_sign_record_id: Mapped[Optional[str]] = mapped_column(String(64))
    platform_active_until: Mapped[Optional[datetime]] = mapped_column(DateTime)


class CampaignItemExclusion(Base, TimestampMixin):
    """Explicit, auditable whole-item exclusion for dedicated non-product links."""

    __tablename__ = "campaign_item_exclusions"

    id: Mapped[int] = mapped_column(primary_key=True)
    taobao_item_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="operator_confirmed")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class CampaignReconReport(Base, TimestampMixin):
    __tablename__ = "campaign_recon_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)  # auto / manual
    # 汇总 (一分不差/贴线/报警计数 + 覆盖缺失/多出 + 立减出入), 逐SKU明细 JSON
    summary: Mapped[Optional[dict]] = mapped_column(JSON)
    rows: Mapped[Optional[list]] = mapped_column(JSON)
    alarm_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class CampaignEvidenceSnapshot(Base, TimestampMixin):
    """Append-only platform evidence for audits and business terminal receipts."""

    __tablename__ = "campaign_evidence_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    workflow_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    evidence_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    web_agent_job_id: Mapped[Optional[str]] = mapped_column(String(64))
    scope_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    result_status: Mapped[str] = mapped_column(String(32), nullable=False)
    platform_summary: Mapped[Optional[dict]] = mapped_column(JSON)
    rows: Mapped[Optional[list]] = mapped_column(JSON)
    failure_rows: Mapped[Optional[list]] = mapped_column(JSON)
    execution_boundary: Mapped[dict] = mapped_column(JSON, nullable=False)
    artifact_kind: Mapped[Optional[str]] = mapped_column(String(64))
    artifact_filename: Mapped[Optional[str]] = mapped_column(String(255))
    artifact_sha256: Mapped[Optional[str]] = mapped_column(String(64))
    artifact_size: Mapped[Optional[int]] = mapped_column(Integer)
    artifact_blob: Mapped[Optional[bytes]] = mapped_column(LargeBinary)
    failure_artifact_filename: Mapped[Optional[str]] = mapped_column(String(255))
    failure_artifact_sha256: Mapped[Optional[str]] = mapped_column(String(64))
    failure_artifact_size: Mapped[Optional[int]] = mapped_column(Integer)
    failure_artifact_blob: Mapped[Optional[bytes]] = mapped_column(LargeBinary)


class CampaignExecutionAttempt(Base, TimestampMixin):
    """Durable one-shot boundary for one exact campaign signup manifest.

    A row is created before the final platform write.  Once ``write_claimed``
    is true, the same workflow/scope can never be automatically submitted
    again, including after a process restart or an unknown browser outcome.
    """

    __tablename__ = "campaign_execution_attempts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    plan_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    workflow_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(32), nullable=False, default="signup")
    scope_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    write_claimed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    write_claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    platform_write_observed: Mapped[Optional[bool]] = mapped_column(Boolean)
    automatic_retry_allowed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False)
    request_id: Mapped[Optional[str]] = mapped_column(String(64), unique=True)
    web_agent_job_id: Mapped[Optional[str]] = mapped_column(String(64))
    last_step: Mapped[Optional[str]] = mapped_column(String(64))
    error_code: Mapped[Optional[str]] = mapped_column(String(128))
    result_summary: Mapped[Optional[dict]] = mapped_column(JSON)

    __table_args__ = (
        UniqueConstraint(
            "workflow_key", "operation", "scope_sha256",
            name="uq_campaign_execution_workflow_operation_scope",
        ),
    )


class CampaignPreparationBundle(Base, TimestampMixin):
    """Immutable, auditable pre-submit package for one campaign scope.

    The bundle contains only ERP calculations and read-only platform evidence.
    Creating it never grants or consumes a platform-write claim.  A later
    executor must revalidate the bundle fingerprint and expiry before it may
    perform the separately guarded final action.
    """

    __tablename__ = "campaign_preparation_bundles"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    plan_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    workflow_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    prepared_by: Mapped[str] = mapped_column(String(128), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_sha256: Mapped[Optional[str]] = mapped_column(String(64))
    identity: Mapped[dict] = mapped_column(JSON, nullable=False)
    summary: Mapped[dict] = mapped_column(JSON, nullable=False)
    signup_rows: Mapped[list] = mapped_column(JSON, nullable=False)
    discount_rows: Mapped[list] = mapped_column(JSON, nullable=False)
    item_decisions: Mapped[list] = mapped_column(JSON, nullable=False)
    gate_results: Mapped[list] = mapped_column(JSON, nullable=False)
    evidence_snapshot_ids: Mapped[list] = mapped_column(JSON, nullable=False)
    execution_boundary: Mapped[dict] = mapped_column(JSON, nullable=False)
    prepared_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    consumed_attempt_id: Mapped[Optional[str]] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint(
            "workflow_key", "source_sha256",
            name="uq_campaign_preparation_workflow_source",
        ),
        UniqueConstraint(
            "workflow_key", "revision",
            name="uq_campaign_preparation_workflow_revision",
        ),
    )


class CampaignSkuSlot(Base, TimestampMixin):
    """One immutable physical Taobao SKU identity in a logical ERP SKU pool."""

    __tablename__ = "campaign_sku_slots"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    taobao_item_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    taobao_sku_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    physical_slot_code: Mapped[str] = mapped_column(String(96), nullable=False, unique=True)
    state: Mapped[str] = mapped_column(String(24), nullable=False, default="active", index=True)
    attribute_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    baseline_daily_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    custom_min_final_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2))
    floor_evidence: Mapped[Optional[dict]] = mapped_column(JSON)
    cooling_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_workflow_key: Mapped[Optional[str]] = mapped_column(String(128))
    active_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    active_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("sku_code", "physical_slot_code",
                         name="uq_campaign_sku_slot_logical_physical"),
    )


class CampaignSkuSlotAttempt(Base, TimestampMixin):
    """One-shot mutation claim for an exact item/SKU-slot manifest."""

    __tablename__ = "campaign_sku_slot_attempts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    workflow_key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    taobao_item_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    sku_code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_slot_id: Mapped[Optional[int]] = mapped_column(Integer)
    target_slot_id: Mapped[Optional[int]] = mapped_column(Integer)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    write_claimed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    request_id: Mapped[Optional[str]] = mapped_column(String(64), unique=True)
    result_summary: Mapped[Optional[dict]] = mapped_column(JSON)

    __table_args__ = (
        UniqueConstraint("workflow_key", "sku_code",
                         name="uq_campaign_sku_slot_attempt_workflow_sku"),
    )


class CampaignCalendar(Base, TimestampMixin):
    __tablename__ = "campaign_calendar"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    start_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    end_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    status: Mapped[Optional[str]] = mapped_column(String(32))          # 千牛展示状态原文
    source: Mapped[str] = mapped_column(String(16), default="discovery", nullable=False)
    # 提醒去重 (spec §四 调度): 同活动只提醒一次/天
    last_notified_on: Mapped[Optional[date]] = mapped_column(Date)

    __table_args__ = (
        UniqueConstraint("title", "start_at", name="uq_campaign_calendar_title_start"),
    )
