"""智能采购询价工作台 API。

所有写接口只改变 ERP 内的任务/执行状态，不直接连接淘宝、1688、拼多多或小红书。
外部执行器确认平台动作成功后，才调用 mark-sent / reply 回写。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.models.auth import User
from app.models.procurement import (
    ProcurementInquiry,
    ProcurementMessage,
    ProcurementTask,
)
from app.services import procurement_service, settings_service

router = APIRouter(prefix="/api/procurement", tags=["procurement"])


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=128)
    category: Literal["daily", "photo", "production"] = "daily"
    item_name: str = Field(min_length=1, max_length=128)
    specification: Optional[str] = Field(default=None, max_length=255)
    quantity: Decimal = Field(default=Decimal("1"), gt=0)
    unit: str = Field(default="件", min_length=1, max_length=16)
    target_unit_price: Optional[Decimal] = Field(default=None, ge=0)
    requirements: Optional[str] = None
    execution_mode: Literal["assisted", "agent"] = "assisted"
    taobao_client_mode: Literal["desktop", "chrome"] = "desktop"
    channels: list[Literal["taobao", "1688", "pinduoduo", "xiaohongshu"]] = ["taobao"]
    channel_daily_limits: dict[str, int] = {
        "taobao": 10, "1688": 5, "pinduoduo": 5, "xiaohongshu": 3,
    }
    followup_intervals_hours: dict[str, int] = {
        "taobao": 12, "1688": 12, "pinduoduo": 12, "xiaohongshu": 24,
    }
    search_queries: list[str] = []
    planned_merchant_count: int = Field(default=10, ge=1, le=50)
    max_followup_rounds: int = Field(default=3, ge=0, le=5)
    ab_test_enabled: bool = True
    ab_test_sample_size: int = Field(default=6, ge=0, le=50)
    script_a: Optional[str] = None
    script_b: Optional[str] = None
    generate_scripts: bool = True

    @model_validator(mode="after")
    def validate_plan(self):
        if not self.channels:
            raise ValueError("至少选择一个采购渠道")
        if self.ab_test_enabled and not (
            2 <= self.ab_test_sample_size <= self.planned_merchant_count
        ):
            raise ValueError("A/B 测试商家数必须在 2 到计划询问数之间")
        return self


class TaskPatch(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=128)
    category: Optional[Literal["daily", "photo", "production"]] = None
    item_name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    specification: Optional[str] = Field(default=None, max_length=255)
    quantity: Optional[Decimal] = Field(default=None, gt=0)
    unit: Optional[str] = Field(default=None, min_length=1, max_length=16)
    target_unit_price: Optional[Decimal] = Field(default=None, ge=0)
    requirements: Optional[str] = None
    execution_mode: Optional[Literal["assisted", "agent"]] = None
    taobao_client_mode: Optional[Literal["desktop", "chrome"]] = None
    channels: Optional[list[Literal["taobao", "1688", "pinduoduo", "xiaohongshu"]]] = None
    channel_daily_limits: Optional[dict[str, int]] = None
    followup_intervals_hours: Optional[dict[str, int]] = None
    search_queries: Optional[list[str]] = None
    planned_merchant_count: Optional[int] = Field(default=None, ge=1, le=50)
    max_followup_rounds: Optional[int] = Field(default=None, ge=0, le=5)
    ab_test_enabled: Optional[bool] = None
    ab_test_sample_size: Optional[int] = Field(default=None, ge=0, le=50)
    script_a: Optional[str] = None
    script_b: Optional[str] = None
    status: Optional[Literal[
        "draft", "ready", "running", "needs_review", "completed", "cancelled"
    ]] = None


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_no: str
    title: str
    category: str
    item_name: str
    specification: Optional[str]
    quantity: Decimal
    unit: str
    target_unit_price: Optional[Decimal]
    requirements: Optional[str]
    search_queries: list[str]
    execution_mode: str
    taobao_client_mode: str
    channels: list[str]
    channel_daily_limits: dict[str, int]
    followup_intervals_hours: dict[str, int]
    planned_merchant_count: int
    max_followup_rounds: int
    ab_test_enabled: bool
    ab_test_sample_size: int
    script_a: Optional[str]
    script_b: Optional[str]
    script_a_ai_draft: Optional[str]
    script_b_ai_draft: Optional[str]
    scripts_reviewed_at: Optional[datetime]
    scripts_reviewed_by: Optional[str]
    winning_variant: Optional[str]
    ai_model: Optional[str]
    ai_suggestion_note: Optional[str]
    status: str
    created_by: Optional[str]
    created_at: datetime
    updated_at: datetime
    counts: dict[str, int] = {}


class MerchantSeed(BaseModel):
    channel: Optional[Literal["taobao", "1688", "pinduoduo", "xiaohongshu"]] = None
    merchant_name: Optional[str] = None
    merchant_url: Optional[str] = None
    product_url: Optional[str] = None
    merchant_external_id: Optional[str] = None


class PrepareQueueIn(BaseModel):
    merchants: list[MerchantSeed] = []


class ReviewScriptsIn(BaseModel):
    script_a: str = Field(min_length=1)
    script_b: Optional[str] = None


class InquiryPatch(BaseModel):
    channel: Optional[Literal["taobao", "1688", "pinduoduo", "xiaohongshu"]] = None
    merchant_name: Optional[str] = Field(default=None, max_length=128)
    merchant_url: Optional[str] = Field(default=None, max_length=1024)
    product_url: Optional[str] = Field(default=None, max_length=1024)
    status: Optional[Literal[
        "discovery_ready", "ready", "waiting_winner", "waiting_reply", "followup_ready",
        "replied", "needs_manual", "completed", "no_reply", "failed",
    ]] = None
    manual_reason: Optional[str] = Field(default=None, max_length=255)


class InquiryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    task_id: int
    slot_no: int
    channel: str
    merchant_name: Optional[str]
    merchant_url: Optional[str]
    product_url: Optional[str]
    merchant_external_id: Optional[str]
    discovery_query: Optional[str]
    discovered_at: Optional[datetime]
    candidate_score: Optional[Decimal]
    candidate_reason: Optional[str]
    candidate_snapshot: dict[str, Any]
    source_rank: Optional[int]
    discovery_attempts: int
    last_discovery_error: Optional[str]
    message_variant: str
    status: str
    followup_round: int
    first_sent_at: Optional[datetime]
    first_response_at: Optional[datetime]
    last_message_at: Optional[datetime]
    next_followup_at: Optional[datetime]
    last_outbound_message: Optional[str]
    last_inbound_message: Optional[str]
    requires_wechat: bool
    wechat_contact: Optional[str]
    manual_reason: Optional[str]
    quote_complete: bool
    quote_amount: Optional[Decimal]
    normalized_unit_price: Optional[Decimal]
    quote_payload: dict[str, Any]
    response_quality: Optional[int]
    decision_status: str
    decision_note: Optional[str]
    decided_at: Optional[datetime]
    decided_by: Optional[str]
    supplier_id: Optional[int]
    part_purchase_id: Optional[int]
    leased_by: Optional[str]
    lease_until: Optional[datetime]
    execution_attempts: int
    last_execution_error: Optional[str]
    last_observed_at: Optional[datetime]
    last_executor_mode: Optional[str]
    approved_message: Optional[str]
    approved_message_base: Optional[str]
    approved_action_key: Optional[str]
    message_reviewed_at: Optional[datetime]
    message_reviewed_by: Optional[str]


class MarkSentIn(BaseModel):
    content: Optional[str] = None
    is_ai_generated: bool = False
    sent_at: Optional[datetime] = None


class ReviewMessageIn(BaseModel):
    content: str = Field(min_length=1)


class ReplyIn(BaseModel):
    content: str = Field(min_length=1)
    received_at: Optional[datetime] = None
    quote_complete: bool = False
    quote_amount: Optional[Decimal] = Field(default=None, ge=0)
    normalized_unit_price: Optional[Decimal] = Field(default=None, ge=0)
    quote_payload: dict[str, Any] = {}
    response_quality: Optional[int] = Field(default=None, ge=0, le=100)
    wechat_contact: Optional[str] = Field(default=None, max_length=128)


class WinnerIn(BaseModel):
    variant: Optional[Literal["A", "B"]] = None


class DecisionIn(BaseModel):
    status: Literal["pending", "shortlisted", "selected", "rejected"]
    note: Optional[str] = None
    supplier_id: Optional[int] = None
    part_purchase_id: Optional[int] = None


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    inquiry_id: int
    direction: str
    round_no: int
    content: str
    is_ai_generated: bool
    requires_manual_review: bool
    event_at: Optional[datetime]
    message_meta: dict[str, Any]


def _task_or_404(db: Session, task_id: int) -> ProcurementTask:
    row = db.get(ProcurementTask, task_id)
    if row is None:
        raise HTTPException(404, "采购询价任务不存在")
    return row


def _inquiry_or_404(db: Session, inquiry_id: int) -> ProcurementInquiry:
    row = db.get(ProcurementInquiry, inquiry_id)
    if row is None:
        raise HTTPException(404, "商家询价记录不存在")
    return row


def _task_out(db: Session, task: ProcurementTask) -> TaskOut:
    data = TaskOut.model_validate(task)
    data.counts = procurement_service.task_counts(db, task.id)
    return data


@router.get("/tasks", response_model=list[TaskOut])
def list_tasks(
    status: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    q = select(ProcurementTask)
    if status:
        q = q.where(ProcurementTask.status == status)
    tasks = db.execute(
        q.order_by(ProcurementTask.id.desc()).limit(limit)
    ).scalars().all()
    return [_task_out(db, task) for task in tasks]


@router.get("/agent-status", response_model=dict)
def get_agent_status(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    status = procurement_service.agent_runtime_status(db)
    status["token_configured"] = bool(
        settings_service.get(
            db, "procurement_agent_token", env_fallback=True
        )
    )
    return status


@router.get("/summary/daily", response_model=dict)
def get_daily_summary(
    summary_date: Optional[date] = None,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return procurement_service.daily_summary(db, summary_date=summary_date)


@router.post("/tasks", response_model=TaskOut)
def create_task(
    payload: TaskCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    data = payload.model_dump(exclude={"generate_scripts"})
    try:
        task = procurement_service.create_task(db, data, created_by=user.username)
        if payload.generate_scripts:
            procurement_service.generate_scripts(db, task)
        db.commit()
        db.refresh(task)
        return _task_out(db, task)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc


@router.get("/tasks/{task_id}", response_model=TaskOut)
def get_task(
    task_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return _task_out(db, _task_or_404(db, task_id))


@router.patch("/tasks/{task_id}", response_model=TaskOut)
def patch_task(
    task_id: int,
    payload: TaskPatch,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    task = _task_or_404(db, task_id)
    changes = payload.model_dump(exclude_unset=True)
    plan_fields = {
        "channels", "planned_merchant_count", "ab_test_enabled", "ab_test_sample_size",
    }
    has_queue = db.execute(
        select(ProcurementInquiry.id)
        .where(ProcurementInquiry.task_id == task_id)
        .limit(1)
    ).scalar_one_or_none() is not None
    if has_queue and plan_fields.intersection(changes):
        raise HTTPException(409, "询价队列已生成，渠道、商家数和 A/B 样本不可再修改")
    for key, value in changes.items():
        if key in {"script_a", "script_b", "requirements", "specification"}:
            value = (value or "").strip() or None
        if key == "search_queries":
            value = procurement_service.clean_search_queries(
                value or [],
                fallback=procurement_service.build_search_queries(
                    task.item_name, task.specification, task.requirements
                ),
            )
        setattr(task, key, value)
    if {"script_a", "script_b"}.intersection(changes):
        task.scripts_reviewed_at = None
        task.scripts_reviewed_by = None
    sample = task.ab_test_sample_size if task.ab_test_enabled else 0
    if task.ab_test_enabled and not 2 <= sample <= task.planned_merchant_count:
        raise HTTPException(400, "A/B 测试商家数必须在 2 到计划询问数之间")
    db.commit()
    db.refresh(task)
    return _task_out(db, task)


@router.post("/tasks/{task_id}/generate-scripts", response_model=dict)
def regenerate_scripts(
    task_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    task = _task_or_404(db, task_id)
    result = procurement_service.generate_scripts(db, task)
    db.commit()
    return result


@router.post("/tasks/{task_id}/review-scripts", response_model=TaskOut)
def review_scripts(
    task_id: int,
    payload: ReviewScriptsIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    task = _task_or_404(db, task_id)
    try:
        procurement_service.review_scripts(
            db,
            task,
            script_a=payload.script_a,
            script_b=payload.script_b,
            reviewed_by=user.username,
        )
        db.commit()
        db.refresh(task)
        return _task_out(db, task)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc


@router.post("/tasks/{task_id}/prepare-queue", response_model=list[InquiryOut])
def prepare_queue(
    task_id: int,
    payload: PrepareQueueIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    task = _task_or_404(db, task_id)
    try:
        rows = procurement_service.prepare_inquiries(
            db, task, [merchant.model_dump() for merchant in payload.merchants]
        )
        db.commit()
        return rows
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc


@router.get("/tasks/{task_id}/inquiries", response_model=list[InquiryOut])
def list_inquiries(
    task_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    _task_or_404(db, task_id)
    return db.execute(
        select(ProcurementInquiry)
        .where(ProcurementInquiry.task_id == task_id)
        .order_by(ProcurementInquiry.slot_no)
    ).scalars().all()


@router.get("/tasks/{task_id}/quotes", response_model=list[dict])
def list_quote_comparison(
    task_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    _task_or_404(db, task_id)
    return procurement_service.quote_comparison(db, task_id)


@router.patch("/inquiries/{inquiry_id}", response_model=InquiryOut)
def patch_inquiry(
    inquiry_id: int,
    payload: InquiryPatch,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    inquiry = _inquiry_or_404(db, inquiry_id)
    task = _task_or_404(db, inquiry.task_id)
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("channel") and changes["channel"] not in (task.channels or []):
        raise HTTPException(400, "该渠道不在任务已选渠道中")
    for key, value in changes.items():
        if isinstance(value, str):
            value = value.strip() or None
        setattr(inquiry, key, value)
    db.commit()
    db.refresh(inquiry)
    return inquiry


@router.post("/inquiries/{inquiry_id}/decision", response_model=InquiryOut)
def decide_inquiry(
    inquiry_id: int,
    payload: DecisionIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    inquiry = _inquiry_or_404(db, inquiry_id)
    try:
        procurement_service.set_inquiry_decision(
            db,
            inquiry,
            status=payload.status,
            decided_by=user.username,
            note=payload.note,
            supplier_id=payload.supplier_id,
            part_purchase_id=payload.part_purchase_id,
        )
        db.commit()
        db.refresh(inquiry)
        return inquiry
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc


@router.get("/tasks/{task_id}/experiment", response_model=dict)
def get_experiment(
    task_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return procurement_service.experiment_metrics(db, _task_or_404(db, task_id))


@router.post("/tasks/{task_id}/apply-winner", response_model=dict)
def select_winner(
    task_id: int,
    payload: WinnerIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    task = _task_or_404(db, task_id)
    try:
        result = procurement_service.apply_winner(db, task, payload.variant)
        db.commit()
        return result
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc


@router.post("/inquiries/{inquiry_id}/mark-sent", response_model=MessageOut)
def mark_sent(
    inquiry_id: int,
    payload: MarkSentIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    inquiry = _inquiry_or_404(db, inquiry_id)
    task = _task_or_404(db, inquiry.task_id)
    try:
        row = procurement_service.mark_message_sent(
            db,
            task,
            inquiry,
            content=payload.content,
            is_ai_generated=payload.is_ai_generated,
            sent_at=payload.sent_at,
        )
        db.commit()
        db.refresh(row)
        return row
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc


@router.post("/inquiries/{inquiry_id}/review-message", response_model=dict)
def review_message(
    inquiry_id: int,
    payload: ReviewMessageIn,
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    inquiry = _inquiry_or_404(db, inquiry_id)
    task = _task_or_404(db, inquiry.task_id)
    try:
        result = procurement_service.review_inquiry_message(
            db,
            task,
            inquiry,
            content=payload.content,
            reviewed_by=user.username,
        )
        db.commit()
        return result
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc


@router.post("/inquiries/{inquiry_id}/reply", response_model=MessageOut)
def record_reply(
    inquiry_id: int,
    payload: ReplyIn,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    inquiry = _inquiry_or_404(db, inquiry_id)
    task = _task_or_404(db, inquiry.task_id)
    try:
        row = procurement_service.record_reply(
            db,
            task,
            inquiry,
            content=payload.content,
            received_at=payload.received_at,
            quote_complete=payload.quote_complete,
            quote_amount=payload.quote_amount,
            normalized_unit_price=payload.normalized_unit_price,
            quote_payload=payload.quote_payload,
            response_quality=payload.response_quality,
            wechat_contact=payload.wechat_contact,
        )
        db.commit()
        db.refresh(row)
        return row
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc


@router.get("/inquiries/{inquiry_id}/messages", response_model=list[MessageOut])
def list_messages(
    inquiry_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    _inquiry_or_404(db, inquiry_id)
    return db.execute(
        select(ProcurementMessage)
        .where(ProcurementMessage.inquiry_id == inquiry_id)
        .order_by(ProcurementMessage.id)
    ).scalars().all()


@router.get("/due-actions", response_model=list[dict])
def list_due_actions(
    task_id: Optional[int] = None,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return procurement_service.due_actions(db, task_id=task_id, limit=limit)
