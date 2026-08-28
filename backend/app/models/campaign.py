"""活动生命周期系统 (2026-07-17 spec: docs/活动生命周期系统_执行plan.md §五)。

三张表:
- CampaignPlan        活动计划 (类型/档期精确到秒/千牛标题/状态机)
- CampaignReconReport 核对报告 (每次核对一行: 汇总 + 逐SKU JSON)
- CampaignCalendar    活动日历 (每日发现的千牛活动, 距开始<3天推飞书, 同活动一天只提醒一次)
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, JSON, Date, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin

# 状态机 (spec §五): draft→precheck→discount_pushed→signup_pushed→reconciled→alarmed
CAMPAIGN_PLAN_STATUSES = (
    "draft", "precheck", "discount_pushed", "signup_pushed", "reconciled", "alarmed",
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
