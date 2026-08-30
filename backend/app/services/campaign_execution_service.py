"""Durable, fail-closed execution boundary for campaign platform writes."""
from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.campaign import CampaignExecutionAttempt


TERMINAL_NO_RETRY_STATES = {
    "write_claimed", "platform_terminal", "completed", "failed_no_retry",
    "unknown_no_retry",
}


def scope_sha256(*, identity: dict, rows: list[dict], policy_sha256: str) -> str:
    payload = {
        "identity": {
            key: identity.get(key) for key in (
                "campaign_title", "campaign_id", "united_activity_id",
                "sign_record_id", "campaign_start", "campaign_end",
                "platform_activity_mode", "official_rate",
            )
        },
        "policy_sha256": str(policy_sha256 or ""),
        "rows": sorted({
            (
                str(row.get("taobao_item_id") or ""),
                str(row.get("taobao_sku_id") or ""),
                str(row.get("price") or ""),
                bool(row.get("is_placeholder")),
            ) for row in rows
        }),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ensure_attempt(
        db: Session, *, plan, scope_sha256_value: str,
        result_summary: dict | None = None) -> tuple[CampaignExecutionAttempt, bool]:
    workflow_key = str(getattr(plan, "workflow_key", None) or "").strip()
    if not workflow_key:
        # Legacy plans created before the workflow-key migration still need a
        # stable one-shot identity.  The persisted plan id is immutable and is
        # safer than disabling the guard for those rows.
        workflow_key = f"campaign:legacy-plan:{int(plan.id)}"
    row = db.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.workflow_key == workflow_key,
        CampaignExecutionAttempt.operation == "signup",
        CampaignExecutionAttempt.scope_sha256 == scope_sha256_value,
    )).scalar_one_or_none()
    if row is not None:
        return row, False
    row = CampaignExecutionAttempt(
        id=secrets.token_hex(12),
        plan_id=int(plan.id),
        workflow_key=workflow_key,
        operation="signup",
        scope_sha256=scope_sha256_value,
        state="prepared",
        write_claimed=False,
        automatic_retry_allowed=False,
        result_summary=result_summary or {},
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        row = db.execute(select(CampaignExecutionAttempt).where(
            CampaignExecutionAttempt.workflow_key == workflow_key,
            CampaignExecutionAttempt.operation == "signup",
            CampaignExecutionAttempt.scope_sha256 == scope_sha256_value,
        )).scalar_one()
        return row, False
    return row, True


def record_prewrite_failure(
        db: Session, attempt: CampaignExecutionAttempt, *, step: str,
        error_code: str, retryable: bool, detail: dict | None = None) -> None:
    if attempt.write_claimed:
        raise ValueError("campaign_execution_write_already_claimed")
    attempt.state = "retryable_prewrite" if retryable else "blocked_prewrite"
    attempt.automatic_retry_allowed = bool(retryable)
    attempt.last_step = step[:64]
    attempt.error_code = error_code[:128]
    attempt.result_summary = detail or {}
    db.commit()


def claim_platform_write(
        db: Session, attempt_id: str, *, request_id: str) -> CampaignExecutionAttempt:
    attempt = db.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.id == attempt_id).with_for_update()).scalar_one()
    if attempt.write_claimed or attempt.state in TERMINAL_NO_RETRY_STATES:
        raise ValueError("campaign_execution_already_claimed_no_retry")
    if attempt.state not in {"prepared", "retryable_prewrite"}:
        raise ValueError("campaign_execution_state_not_claimable")
    attempt.state = "write_claimed"
    attempt.write_claimed = True
    attempt.write_claimed_at = datetime.now(timezone.utc)
    attempt.automatic_retry_allowed = False
    attempt.request_id = request_id[:64]
    attempt.last_step = "platform_write_claim"
    attempt.error_code = None
    db.commit()
    return attempt


def record_platform_terminal(
        db: Session, attempt: CampaignExecutionAttempt, *, state: str,
        platform_write_observed: bool | None, step: str,
        error_code: str | None = None, job_id: str | None = None,
        result_summary: dict | None = None) -> None:
    if not attempt.write_claimed:
        raise ValueError("campaign_execution_terminal_without_claim")
    attempt.state = state
    attempt.platform_write_observed = platform_write_observed
    attempt.automatic_retry_allowed = False
    attempt.last_step = step[:64]
    attempt.error_code = error_code[:128] if error_code else None
    attempt.web_agent_job_id = str(job_id or "")[:64] or None
    attempt.result_summary = result_summary or {}
    db.commit()


def failure_is_retryable_prewrite(result: dict) -> bool:
    """Retry only connectivity/startup failures before any write claim."""
    text = " ".join(str(result.get(key) or "") for key in (
        "error", "message", "detail", "step"))
    retryable = (
        "ReadTimeout", "ConnectTimeout", "ConnectionError", "No route to host",
        "Failed to establish a new connection", "exited during startup",
        "8500", "取数服务未能按需启动", "Web-Agent 未在线",
    )
    interaction = ("need_scan", "二维码", "验证码", "登录", "token 无效")
    return any(marker in text for marker in retryable) and not any(
        marker in text for marker in interaction)
