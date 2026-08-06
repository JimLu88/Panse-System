"""采购 Windows sidecar 的机器接口。

使用独立 ``procurement_agent_token``（请求头 ``X-API-Key``）。接口只允许领取
ERP 已批准的 agent 模式任务并回写可审计结果，不接收任何平台账号密码/cookie。
"""
from __future__ import annotations

import hmac
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.procurement import ProcurementInquiry
from app.services import procurement_service, settings_service

router = APIRouter(prefix="/api/procurement/agent", tags=["procurement-agent"])

TOKEN_KEY = "procurement_agent_token"


def require_agent_token(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> str:
    expected = settings_service.get(db, TOKEN_KEY, env_fallback=True)
    if not expected:
        raise HTTPException(503, "采购执行器令牌未配置")
    if not x_api_key or not hmac.compare_digest(x_api_key.strip(), expected.strip()):
        raise HTTPException(401, "采购执行器令牌无效")
    return x_api_key


class HeartbeatIn(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64)
    display_name: Optional[str] = Field(default=None, max_length=128)
    host_label: Optional[str] = Field(default=None, max_length=128)
    version: Optional[str] = Field(default=None, max_length=32)
    mode: Literal["dry_run", "review", "live"] = "dry_run"
    status: Literal["online", "busy", "paused", "error"] = "online"
    capabilities: list[str] = []
    current_inquiry_id: Optional[int] = None
    last_error: Optional[str] = None
    counters: dict[str, Any] = {}


class ClaimIn(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64)
    mode: Literal["dry_run", "review", "live"] = "dry_run"
    capabilities: list[str] = []
    max_actions: int = Field(default=1, ge=1, le=10)
    lease_seconds: int = Field(default=180, ge=60, le=900)


class SentIn(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64)
    lease_token: str = Field(min_length=1, max_length=64)
    content: str = Field(min_length=1)
    external_message_id: str = Field(min_length=1, max_length=255)
    external_thread_id: Optional[str] = Field(default=None, max_length=255)
    sent_at: Optional[datetime] = None


class ObservedReplyIn(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64)
    content: str = Field(min_length=1)
    external_message_id: str = Field(min_length=1, max_length=255)
    received_at: Optional[datetime] = None
    quote_complete: bool = False
    quote_amount: Optional[Decimal] = Field(default=None, ge=0)
    normalized_unit_price: Optional[Decimal] = Field(default=None, ge=0)
    quote_payload: dict[str, Any] = {}
    response_quality: Optional[int] = Field(default=None, ge=0, le=100)
    wechat_contact: Optional[str] = Field(default=None, max_length=128)


class FailureIn(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64)
    lease_token: str = Field(min_length=1, max_length=64)
    error: str = Field(min_length=1)
    retryable: bool = True


class ManualHandoffIn(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64)
    lease_token: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=255)


class WatchIn(BaseModel):
    capabilities: list[str] = []


def _inquiry_or_404(db: Session, inquiry_id: int) -> ProcurementInquiry:
    row = db.get(ProcurementInquiry, inquiry_id)
    if row is None:
        raise HTTPException(404, "商家询价记录不存在")
    return row


@router.post("/heartbeat", response_model=dict)
def heartbeat(
    payload: HeartbeatIn,
    db: Session = Depends(get_db),
    _: str = Depends(require_agent_token),
):
    try:
        row = procurement_service.heartbeat_agent(db, **payload.model_dump())
        db.commit()
        return {
            "ok": True,
            "agent_id": row.agent_id,
            "server_time": procurement_service.utcnow().isoformat(),
        }
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc


@router.post("/claim", response_model=dict)
def claim(
    payload: ClaimIn,
    db: Session = Depends(get_db),
    _: str = Depends(require_agent_token),
):
    try:
        procurement_service.heartbeat_agent(
            db,
            agent_id=payload.agent_id,
            mode=payload.mode,
            capabilities=payload.capabilities,
            status="online",
        )
        actions = procurement_service.claim_agent_actions(db, **payload.model_dump())
        db.commit()
        return {
            "ok": True,
            "mode": payload.mode,
            "actions": actions,
            "claimed": 0 if payload.mode == "dry_run" else len(actions),
            "previewed": len(actions) if payload.mode == "dry_run" else 0,
        }
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc


@router.post("/inquiries/{inquiry_id}/sent", response_model=dict)
def sent(
    inquiry_id: int,
    payload: SentIn,
    db: Session = Depends(get_db),
    _: str = Depends(require_agent_token),
):
    inquiry = _inquiry_or_404(db, inquiry_id)
    try:
        row, duplicate = procurement_service.confirm_agent_sent(
            db, inquiry=inquiry, **payload.model_dump()
        )
        db.commit()
        return {
            "ok": True,
            "duplicate": duplicate,
            "message_id": row.id,
            "inquiry_status": inquiry.status,
        }
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc


@router.post("/inquiries/{inquiry_id}/reply", response_model=dict)
def observed_reply(
    inquiry_id: int,
    payload: ObservedReplyIn,
    db: Session = Depends(get_db),
    _: str = Depends(require_agent_token),
):
    inquiry = _inquiry_or_404(db, inquiry_id)
    try:
        row, duplicate = procurement_service.record_agent_reply(
            db, inquiry=inquiry, **payload.model_dump()
        )
        db.commit()
        return {
            "ok": True,
            "duplicate": duplicate,
            "message_id": row.id,
            "inquiry_status": inquiry.status,
            "requires_wechat": inquiry.requires_wechat,
            "manual_reason": inquiry.manual_reason,
        }
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc


@router.post("/inquiries/{inquiry_id}/failure", response_model=dict)
def failure(
    inquiry_id: int,
    payload: FailureIn,
    db: Session = Depends(get_db),
    _: str = Depends(require_agent_token),
):
    inquiry = _inquiry_or_404(db, inquiry_id)
    try:
        row = procurement_service.agent_failure(
            db, inquiry=inquiry, **payload.model_dump()
        )
        db.commit()
        return {
            "ok": True,
            "status": row.status,
            "attempts": row.execution_attempts,
            "manual_reason": row.manual_reason,
        }
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc


@router.post("/inquiries/{inquiry_id}/manual", response_model=dict)
def manual_handoff(
    inquiry_id: int,
    payload: ManualHandoffIn,
    db: Session = Depends(get_db),
    _: str = Depends(require_agent_token),
):
    inquiry = _inquiry_or_404(db, inquiry_id)
    try:
        row = procurement_service.agent_manual_handoff(
            db, inquiry=inquiry, **payload.model_dump()
        )
        db.commit()
        return {"ok": True, "status": row.status, "manual_reason": row.manual_reason}
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc


@router.post("/watch", response_model=dict)
def watch(
    payload: WatchIn,
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    _: str = Depends(require_agent_token),
):
    return {
        "ok": True,
        "conversations": procurement_service.agent_watch_list(
            db, capabilities=payload.capabilities, limit=limit
        ),
    }
