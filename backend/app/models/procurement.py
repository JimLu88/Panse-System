"""智能采购询价模型。

采购任务与已经成交/入库的 ``PartPurchase`` 分开：
- ProcurementTask：一次询价计划、话术实验和渠道规则
- ProcurementInquiry：计划内的一家商家/一个待询价槽位
- ProcurementMessage：往来消息审计记录

这里仅记录“待发送/已发送/待追问”等执行状态；真正的淘宝、1688、拼多多、小红书
桌面代理是独立执行器，避免 ERP 保存任务时意外对外发消息或下单。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class ProcurementTask(Base, TimestampMixin):
    """一次采购询价计划。"""

    __tablename__ = "procurement_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_no: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[str] = mapped_column(String(24), nullable=False, default="daily", index=True)
    # daily=日常配件 photo=拍摄搭配 production=生产材料
    item_name: Mapped[str] = mapped_column(String(128), nullable=False)
    specification: Mapped[Optional[str]] = mapped_column(String(255))
    quantity: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, default=Decimal("1")
    )
    unit: Mapped[str] = mapped_column(String(16), nullable=False, default="件")
    target_unit_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4))
    requirements: Mapped[Optional[str]] = mapped_column(Text)
    search_queries: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    execution_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="assisted"
    )
    # assisted=ERP 出建议、人手发送；agent=外部代理取队列执行并回写
    taobao_client_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="desktop"
    )
    # desktop=淘宝桌面版；chrome=Chrome 独立采购账号
    channels: Mapped[list] = mapped_column(
        JSON, nullable=False, default=lambda: ["taobao"]
    )
    channel_daily_limits: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=lambda: {"taobao": 10, "1688": 5, "xiaohongshu": 3},
    )
    followup_intervals_hours: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
        default=lambda: {"taobao": 12, "1688": 12, "xiaohongshu": 24},
    )
    planned_merchant_count: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    max_followup_rounds: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    ab_test_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    ab_test_sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=6)
    script_a: Mapped[Optional[str]] = mapped_column(Text)
    script_b: Mapped[Optional[str]] = mapped_column(Text)
    script_a_ai_draft: Mapped[Optional[str]] = mapped_column(Text)
    script_b_ai_draft: Mapped[Optional[str]] = mapped_column(Text)
    scripts_reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    scripts_reviewed_by: Mapped[Optional[str]] = mapped_column(String(64))
    winning_variant: Mapped[Optional[str]] = mapped_column(String(8))
    ai_model: Mapped[Optional[str]] = mapped_column(String(128))
    ai_suggestion_note: Mapped[Optional[str]] = mapped_column(Text)

    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="draft", index=True
    )
    # draft / ready / running / needs_review / completed / cancelled
    created_by: Mapped[Optional[str]] = mapped_column(String(64))

    __table_args__ = (
        Index("ix_procurement_tasks_status_created", "status", "created_at"),
    )


class ProcurementInquiry(Base, TimestampMixin):
    """计划内的一家商家；商家未录入时可先作为待填充槽位。"""

    __tablename__ = "procurement_inquiries"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("procurement_tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    slot_no: Mapped[int] = mapped_column(Integer, nullable=False)
    channel: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    merchant_name: Mapped[Optional[str]] = mapped_column(String(128))
    merchant_url: Mapped[Optional[str]] = mapped_column(String(1024))
    product_url: Mapped[Optional[str]] = mapped_column(String(1024))
    merchant_external_id: Mapped[Optional[str]] = mapped_column(String(255))

    # 搜索发现信息。空槽位由 Windows 执行器搜索后直接补成候选商家，
    # 不另建第二套候选表。
    discovery_query: Mapped[Optional[str]] = mapped_column(String(255))
    discovered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    candidate_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    candidate_reason: Mapped[Optional[str]] = mapped_column(Text)
    candidate_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    candidate_dedupe_key: Mapped[Optional[str]] = mapped_column(String(64))
    source_rank: Mapped[Optional[int]] = mapped_column(Integer)
    discovery_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_discovery_error: Mapped[Optional[str]] = mapped_column(Text)

    message_variant: Mapped[str] = mapped_column(
        String(16), nullable=False, default="winner_pending", index=True
    )
    # A / B / winner_pending / manual
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="ready", index=True
    )
    # ready / waiting_winner / waiting_reply / replied / needs_manual /
    # completed / no_reply / failed
    followup_round: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    first_response_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_message_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    next_followup_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), index=True
    )
    last_outbound_message: Mapped[Optional[str]] = mapped_column(Text)
    last_inbound_message: Mapped[Optional[str]] = mapped_column(Text)

    requires_wechat: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    wechat_contact: Mapped[Optional[str]] = mapped_column(String(128))
    manual_reason: Mapped[Optional[str]] = mapped_column(String(255))

    quote_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    quote_amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4))
    normalized_unit_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 4))
    quote_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    response_quality: Mapped[Optional[int]] = mapped_column(Integer)

    # 只记录人工采购决策及与既有采购单的关联；不会触发下单或付款。
    decision_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", index=True
    )
    decision_note: Mapped[Optional[str]] = mapped_column(Text)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[Optional[str]] = mapped_column(String(64))
    supplier_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("suppliers.id", ondelete="SET NULL"), index=True
    )
    part_purchase_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("part_purchases.id", ondelete="SET NULL"), index=True
    )

    # 外部执行器领取租约：一次只允许一个 agent 操作同一商家，超时可自动回收。
    lease_token: Mapped[Optional[str]] = mapped_column(String(64), unique=True)
    leased_by: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    lease_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    execution_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    external_thread_id: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    external_message_id: Mapped[Optional[str]] = mapped_column(String(255), index=True)
    last_execution_error: Mapped[Optional[str]] = mapped_column(Text)
    last_observed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_executor_mode: Mapped[Optional[str]] = mapped_column(String(16))

    # 每一次对外消息都可以在 ERP 中单独改稿。首轮话术先经过任务级审核；
    # 追问必须有与当前轮次匹配的人工确认稿，执行器才允许领取。
    approved_message: Mapped[Optional[str]] = mapped_column(Text)
    approved_message_base: Mapped[Optional[str]] = mapped_column(Text)
    approved_action_key: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    message_reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    message_reviewed_by: Mapped[Optional[str]] = mapped_column(String(64))

    __table_args__ = (
        Index("ix_procurement_inquiries_task_slot", "task_id", "slot_no", unique=True),
        Index(
            "ix_procurement_inquiries_due",
            "status",
            "next_followup_at",
        ),
        Index(
            "uq_procurement_inquiries_task_candidate",
            "task_id",
            "candidate_dedupe_key",
            unique=True,
        ),
    )


class ProcurementMessage(Base, TimestampMixin):
    """询价消息审计；执行器回写成功后才将 outbound 记为已发送。"""

    __tablename__ = "procurement_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    inquiry_id: Mapped[int] = mapped_column(
        ForeignKey("procurement_inquiries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    direction: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    # outbound / inbound / system
    round_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_ai_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    requires_manual_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    event_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    message_meta: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    external_message_id: Mapped[Optional[str]] = mapped_column(String(255))

    __table_args__ = (
        UniqueConstraint(
            "inquiry_id",
            "direction",
            "external_message_id",
            name="uq_procurement_message_external",
        ),
    )


class ProcurementAgentState(Base, TimestampMixin):
    """Windows 采购执行器心跳，不保存账号密码或平台 cookie。"""

    __tablename__ = "procurement_agent_states"

    id: Mapped[int] = mapped_column(primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(128))
    host_label: Mapped[Optional[str]] = mapped_column(String(128))
    version: Mapped[Optional[str]] = mapped_column(String(32))
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="dry_run")
    # dry_run / review / live；live 必须在 sidecar 本地显式开启，ERP 不可远程提权。
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="online")
    capabilities: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    current_inquiry_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("procurement_inquiries.id", ondelete="SET NULL"),
        index=True,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_error: Mapped[Optional[str]] = mapped_column(Text)
    counters: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
