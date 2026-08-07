"""Normalize critical automation failures from the append-only scheduler log."""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.scheduled_job import ScheduledJobRun


LOCAL_TZ = ZoneInfo("Asia/Shanghai")
CATEGORY_LABELS = {
    "order": "订单拉取与推送",
    "finance": "余额和收支拉取",
    "campaign": "自动报活动",
}
JOB_CATEGORIES = {
    "order_delivery_recovery": "order",
    "daily_0630_web_agent": "order",
    "daily_1810_order_sheets": "order",
    "pull_catchup_30min": "order",
    "email_poll_alipay_6h": "finance",
    "daily_2030_finance_agent": "finance",
    "finance_pull_retry_30min": "finance",
    "finance_pull_retry_2200": "finance",
    "campaign_daily_discovery": "campaign",
    "campaign_auto_execute": "campaign",
    "campaign_auto_recon": "campaign",
    "campaign_price_protection_rule_remind": "campaign",
    "daily_0830_promo_price_check": "campaign",
}


def record_callback_run(
    db: Session,
    *,
    category: str,
    status: str,
    detail: str,
    recovery_key: str,
    result_summary: Optional[dict[str, Any]] = None,
    batch_id: Optional[str] = None,
    business_date: Optional[str] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Persist non-scheduler recovery work in the same append-only run log.

    Password and scan callbacks can finish an order chain outside APScheduler.
    Without an explicit successful run, the failure recorder keeps earlier
    scheduler failures open even though delivery already recovered.
    """
    if category not in CATEGORY_LABELS:
        raise ValueError(f"unknown category: {category}")
    if status not in {"ok", "fail"}:
        raise ValueError(f"invalid callback status: {status}")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    job_id = f"{category}_delivery_recovery" if category == "order" else f"{category}_recovery"

    recent = db.execute(
        select(ScheduledJobRun)
        .where(ScheduledJobRun.job_id == job_id)
        .order_by(ScheduledJobRun.id.desc())
        .limit(20)
    ).scalars().all()
    for row in recent:
        summary = row.result_summary if isinstance(row.result_summary, dict) else {}
        if summary.get("recovery_key") == recovery_key and row.status == status:
            return {"created": False, "run_id": row.id, "status": row.status}

    summary = {
        "callback": True,
        "recovery_key": recovery_key,
        **(result_summary or {}),
    }
    if batch_id:
        summary["order_batch_id"] = batch_id
    if business_date:
        summary["order_business_date"] = business_date
    row = ScheduledJobRun(
        job_id=job_id,
        job_label=f"{CATEGORY_LABELS[category]}恢复回调",
        status=status,
        duration_ms=0,
        detail=detail,
        error=detail if status == "fail" else None,
        result_summary=summary,
        started_at=current,
        completed_at=current,
    )
    db.add(row)
    db.flush()
    return {"created": True, "run_id": row.id, "status": row.status}


def _recovery_key(job_id: str) -> str:
    """Order/finance retries use different job IDs but belong to one daily chain."""
    category = JOB_CATEGORIES[job_id]
    return category if category in {"order", "finance"} else job_id


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(LOCAL_TZ).isoformat()


def _pipeline_blocks(summary: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        value for key, value in summary.items()
        if key.endswith("_pipeline") and isinstance(value, dict)
    ]


def _next_retry_at(summary: dict[str, Any]) -> Optional[str]:
    candidates = [str(summary["next_retry_at"])] if summary.get("next_retry_at") else []
    candidates.extend(
        str(block["next_retry_at"])
        for block in _pipeline_blocks(summary)
        if block.get("next_retry_at")
    )
    return min(candidates) if candidates else None


def _source_failures(summary: dict[str, Any]) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    for item in summary.get("tasks") or []:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").lower()
        if status not in {"error", "failed", "fail", "pending_manual"}:
            continue
        failures.append({
            "task": str(item.get("task") or item.get("id") or "unknown"),
            "status": status,
            "reason": str(item.get("error") or item.get("reason") or "未返回原因"),
        })
    for item in summary.get("pending_manual") or []:
        if isinstance(item, dict):
            failures.append({
                "task": str(item.get("task") or item.get("id") or "unknown"),
                "status": "pending_manual",
                "reason": str(item.get("reason") or "需要人工处理"),
            })
    return failures


def _batch_id(summary: dict[str, Any]) -> Optional[str]:
    """Read a business batch id from scheduler/callback result summaries."""
    for key in ("order_batch_id", "batch_id"):
        value = str(summary.get(key) or "").strip()
        if value:
            return value
    delivery = summary.get("delivery")
    if isinstance(delivery, dict):
        for key in ("order_batch_id", "batch_id"):
            value = str(delivery.get(key) or "").strip()
            if value:
                return value
    return None


def list_failure_events(
    db: Session,
    *,
    on: Optional[date] = None,
    category: Optional[str] = None,
    limit: int = 500,
) -> dict[str, Any]:
    """Return every failed run for one Beijing calendar day without deduping."""
    if category is not None and category not in CATEGORY_LABELS:
        raise ValueError(f"unknown category: {category}")
    on = on or datetime.now(LOCAL_TZ).date()
    start = datetime.combine(on, time.min, tzinfo=LOCAL_TZ).astimezone(timezone.utc)
    end = datetime.combine(on, time.max, tzinfo=LOCAL_TZ).astimezone(timezone.utc)
    job_ids = [
        job_id for job_id, item_category in JOB_CATEGORIES.items()
        if category is None or item_category == category
    ]
    # Failures are selected from the requested Beijing day. Later rows are
    # included only as recovery evidence, so a password callback shortly after
    # midnight can close the same immutable batch without hiding a new day's
    # unrelated failure.
    recovery_end = max(end, datetime.now(timezone.utc))
    rows = db.execute(
        select(ScheduledJobRun)
        .where(
            ScheduledJobRun.job_id.in_(job_ids),
            ScheduledJobRun.started_at >= start,
            ScheduledJobRun.started_at <= recovery_end,
        )
        .order_by(ScheduledJobRun.started_at.asc(), ScheduledJobRun.id.asc())
    ).scalars().all()

    attempts: dict[str, int] = {}
    events: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        row_started = row.started_at
        if row_started is not None and row_started.tzinfo is None:
            row_started = row_started.replace(tzinfo=timezone.utc)
        if row.status != "fail" or row_started is None or row_started > end:
            continue
        recovery_key = _recovery_key(row.job_id)
        summary = row.result_summary if isinstance(row.result_summary, dict) else {}
        batch_id = _batch_id(summary)
        attempt_key = f"{recovery_key}:{batch_id or on.isoformat()}"
        attempts[attempt_key] = attempts.get(attempt_key, 0) + 1
        later_success = next((
            later for later in rows[index + 1:]
            if _recovery_key(later.job_id) == recovery_key
            and later.status == "ok"
            and (
                _batch_id(
                    later.result_summary
                    if isinstance(later.result_summary, dict) else {}
                ) == batch_id
                if batch_id
                else (
                    later.started_at is not None
                    and (
                        later.started_at.replace(tzinfo=timezone.utc)
                        if later.started_at.tzinfo is None else later.started_at
                    ) <= end
                )
            )
        ), None)
        blocks = _pipeline_blocks(summary)
        final = bool(summary.get("final")) or any(bool(x.get("final")) for x in blocks)
        waiting_input = bool(summary.get("waiting") or summary.get("pending_manual")) or any(
            bool(x.get("waiting_input")) for x in blocks
        )
        state = (
            "recovered" if later_success else
            "final" if final else
            "waiting_input" if waiting_input else
            "open"
        )
        item_category = JOB_CATEGORIES[row.job_id]
        events.append({
            "id": row.id,
            "date": on.isoformat(),
            "category": item_category,
            "category_label": CATEGORY_LABELS[item_category],
            "job_id": row.job_id,
            "job_label": row.job_label,
            "attempt_no": attempts[attempt_key],
            "batch_id": batch_id,
            "business_date": summary.get("order_business_date"),
            "reason": row.error or str(summary.get("_error") or "任务失败但未返回原因"),
            "state": state,
            "final": final,
            "waiting_input": waiting_input,
            "next_retry_at": _next_retry_at(summary),
            "recovered_at": _iso(later_success.completed_at) if later_success else None,
            "started_at": _iso(row.started_at),
            "completed_at": _iso(row.completed_at),
            "duration_ms": row.duration_ms,
            "source_failures": _source_failures(summary),
            "result_summary": summary,
        })

    events = list(reversed(events))[: max(1, min(limit, 2000))]
    by_category = {key: 0 for key in CATEGORY_LABELS}
    for event in events:
        by_category[event["category"]] += 1
    return {
        "date": on.isoformat(),
        "total": len(events),
        "open_count": sum(item["state"] != "recovered" for item in events),
        "by_category": by_category,
        "items": events,
    }
