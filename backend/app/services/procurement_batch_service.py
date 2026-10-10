"""Bounded procurement batches. Database only; never controls a platform.

P1 foundation: fixed clock, immutable requirement snapshot and cutoff evidence.
Until P2 supplies an atomic send-intent/permit protocol, bounded tasks are NOT
consumable by legacy review/live agents. This is not autonomous procurement.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.procurement import ProcurementInquiry, ProcurementMessage, ProcurementTask

POLICY_VERSION = "48h-v1"


def utc(value: datetime) -> datetime:
    # SQLite roundtrips timezone-aware UTC columns as naive datetimes.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def json_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return utc(value).isoformat()
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    return value


def bounded(task: ProcurementTask) -> bool:
    return task.batch_policy_version is not None


def lock_task(db: Session, task: ProcurementTask) -> ProcurementTask:
    return db.scalars(select(ProcurementTask).where(ProcurementTask.id == task.id)
                      .with_for_update().execution_options(populate_existing=True)).one()


def window_open(task: ProcurementTask, *, now: datetime | None = None) -> bool:
    if not bounded(task):
        return True
    now = utc(now or now_utc())
    return bool(
        task.batch_policy_version == POLICY_VERSION
        and task.started_at and task.deadline_at and not task.closed_at
        and task.status in {"ready", "running"}
        and utc(task.started_at) <= now < utc(task.deadline_at)
    )


def require_draft(task: ProcurementTask) -> None:
    if bounded(task) and (task.started_at or task.status in {"cancelled", "expired", "completed"}):
        raise ValueError("批次已启动或关闭，需求和策略已冻结；不能原位改写或重启计时")


def activate(db: Session, task: ProcurementTask, *, activated_by: str, now: datetime | None = None):
    if task.batch_policy_version != POLICY_VERSION:
        raise ValueError("旧任务不自动转换为 48 小时批次，请新建需求")
    task = lock_task(db, task)
    now = utc(now or now_utc())
    if task.started_at:
        return task  # Repeated activation never resets the clock, even after expiry.
    if task.status != "ready" or not task.scripts_reviewed_at:
        raise ValueError("请先确认资料和话术，并准备完整商家队列")
    count = db.scalar(select(func.count()).select_from(ProcurementInquiry).where(
        ProcurementInquiry.task_id == task.id))
    if count != task.planned_merchant_count:
        raise ValueError("商家队列数量与计划不一致")
    if not (task.specification or "").strip() or not (task.requirements or "").strip() or task.quantity <= 0:
        raise ValueError("请补齐规格、数量和需求说明，不能猜测缺失资料")
    from app.services.procurement_dispatch_service import snapshot_bindings
    snapshot = json_value({
        "version": POLICY_VERSION, "activated_by": activated_by,
        "item_name": task.item_name, "specification": task.specification,
        "quantity": task.quantity, "unit": task.unit, "requirements": task.requirements,
        "planned_merchant_count": task.planned_merchant_count, "channels": task.channels,
        "execution_mode": task.execution_mode, "taobao_client_mode": task.taobao_client_mode,
        "target_unit_price": task.target_unit_price, "search_queries": task.search_queries,
        "channel_daily_limits": task.channel_daily_limits,
        "followup_intervals_hours": task.followup_intervals_hours,
        "max_followup_rounds": task.max_followup_rounds, "ab_test_enabled": task.ab_test_enabled,
        "ab_test_sample_size": task.ab_test_sample_size,
        "script_a": task.script_a, "script_b": task.script_b,
        "window_hours": 48, "orders_allowed": False, "payments_allowed": False,
        "agent_execution_ready": False,
        "account_bindings": snapshot_bindings(db, task.id),
        "dispatch_protocol": "dispatch-v1", "quiet_hours_beijing": "20:00-09:00",
    })
    # CAS also protects databases where SELECT FOR UPDATE is unavailable.
    db.execute(update(ProcurementTask).where(
        ProcurementTask.id == task.id, ProcurementTask.started_at.is_(None),
        ProcurementTask.status == "ready", ProcurementTask.batch_policy_version == POLICY_VERSION,
    ).values(started_at=now, deadline_at=now + timedelta(hours=48),
             policy_snapshot=snapshot, status="running"), execution_options={"synchronize_session": False})
    db.flush()
    db.refresh(task)
    if not task.started_at:
        raise ValueError("批次状态已改变，请刷新后核对")
    return task


def _report(db: Session, task: ProcurementTask, *, cutoff: datetime, generated_at: datetime, reason: str):
    # Use immutable messages with BOTH observed time and server recording time
    # inside the window. A late/backdated callback cannot rewrite the cutoff.
    messages = db.scalars(select(ProcurementMessage).join(ProcurementInquiry).where(
        ProcurementInquiry.task_id == task.id,
    ).order_by(ProcurementMessage.id)).all()
    eligible = []
    invalid_receipts = 0
    for msg in messages:
        recorded = (msg.message_meta or {}).get("server_recorded_at")
        if not recorded or not msg.event_at:
            continue  # Old evidence without receipt time is explicitly excluded.
        try:
            received = utc(datetime.fromisoformat(recorded))
        except (TypeError, ValueError):
            invalid_receipts += 1
            continue  # Do not invent receipt time or block all batches on bad legacy metadata.
        if utc(task.started_at) <= utc(msg.event_at) < cutoff and utc(task.started_at) <= received < cutoff:
            eligible.append(msg)
    sent = {m.inquiry_id for m in eligible if m.direction == "outbound" and (m.message_meta or {}).get("confirmed_sent")}
    replies = {m.inquiry_id for m in eligible if m.direction == "inbound"}
    latest_quotes = {}
    for msg in eligible:
        if msg.direction == "inbound":
            latest_quotes[msg.inquiry_id] = {
                "inquiry_id": msg.inquiry_id, "message_id": msg.id,
                "event_at": utc(msg.event_at).isoformat(),
                "quote": (msg.message_meta or {}).get("quote_evidence") or {},
            }
    return {
        "schema_version": 1, "kind": "cutoff_evidence_summary", "reason": reason,
        "task_id": task.id, "started_at": utc(task.started_at).isoformat(),
        "deadline_at": utc(task.deadline_at).isoformat(), "as_of": cutoff.isoformat(),
        "generated_at": generated_at.isoformat(),
        "generation_delay_seconds": max(0, (generated_at - cutoff).total_seconds()),
        "planned_merchant_count": task.planned_merchant_count,
        "confirmed_sent_merchants": len(sent), "replied_merchants": len(replies),
        "not_confirmed_sent_merchants": max(0, task.planned_merchant_count - len(sent)),
        "evidence_message_ids": [m.id for m in eligible], "quote_evidence": list(latest_quotes.values()),
        "invalid_receipt_count": invalid_receipts,
        "recommendations_ready": False,
        "limitations": ["基础证据快照，不是最终前五推荐", "未确认发送不等于未发送；不能据此补发", "账号配额和原子发送意图尚未接入真实执行器"],
    }


def close_batch(db: Session, task: ProcurementTask, *, now: datetime | None = None, cancel=False) -> bool:
    task = lock_task(db, task)
    now = utc(now or now_utc())
    if not bounded(task) or not task.started_at or task.closed_at:
        return False
    if not cancel and utc(task.deadline_at) > now:
        return False
    cutoff = min(now, utc(task.deadline_at)) if cancel else utc(task.deadline_at)
    report = _report(db, task, cutoff=cutoff, generated_at=now, reason="cancelled" if cancel else "deadline")
    changed = db.execute(update(ProcurementTask).where(
        ProcurementTask.id == task.id, ProcurementTask.closed_at.is_(None),
    ).values(closed_at=now, deadline_report=report, status="cancelled" if cancel else "expired"),
        execution_options={"synchronize_session": False}).rowcount
    # Do not erase leases or unknown attempts: a late receipt must stay traceable.
    db.flush()
    db.refresh(task)
    return bool(changed)


def close_due_batches(db: Session, *, now: datetime | None = None) -> dict:
    now = utc(now or now_utc())
    tasks = db.scalars(select(ProcurementTask).where(
        ProcurementTask.batch_policy_version == POLICY_VERSION,
        ProcurementTask.deadline_at <= now, ProcurementTask.closed_at.is_(None),
    ).order_by(ProcurementTask.deadline_at).limit(100)).all()
    return {"closed": sum(close_batch(db, task, now=now) for task in tasks), "platform_actions": 0}


def is_supplement(task: ProcurementTask, *, now: datetime | None = None) -> bool:
    return bool(bounded(task) and task.started_at and (
        task.closed_at or task.status == "cancelled" or utc(now or now_utc()) >= utc(task.deadline_at)))
