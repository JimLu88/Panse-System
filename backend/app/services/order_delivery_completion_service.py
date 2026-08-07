"""Shared close-out for order delivery recovered outside the scheduler."""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import Iterable

from sqlalchemy.orm import Session


ORDER_RECOVERY_PUSH_LIMIT = 500
ORDER_RETRY_TIMES = ((19, 17), (20, 17), (21, 17))


def _retry_slots(now: datetime | None = None) -> list[datetime]:
    current = (now or datetime.now()).astimezone()
    return [
        current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        for hour, minute in ORDER_RETRY_TIMES
    ]


def _recovery_key(source: str, manifest: Iterable[str]) -> str:
    material = source + "\n" + "\n".join(sorted(str(item) for item in manifest))
    return sha256(material.encode("utf-8")).hexdigest()


def complete_recovered_order_delivery(
    db: Session,
    *,
    source: str,
    manifest: list[str],
) -> dict:
    """Deliver every pending image, sync the factory table and close evidence.

    The function is intentionally shared by the shipping-password callback and
    manual scan recovery.  It uses pushed markers for idempotency and a bounded
    500-image batch so a late password does not strand rows behind the former
    50-image callback limit.
    """
    from app.services import (
        automation_failure_recorder_service,
        automation_pipeline_service,
        factory_dispatch_feishu_service,
        order_sheet_archive_service,
    )

    key = _recovery_key(source, manifest)
    try:
        delivery = order_sheet_archive_service.reconcile_pending_delivery(
            db, limit=ORDER_RECOVERY_PUSH_LIMIT, quiet=True,
        )
    except Exception as exc:  # noqa: BLE001 - turn callback crashes into durable evidence
        db.rollback()
        delivery = {
            "_run_status": "fail",
            "_error": f"delivery_exception: {type(exc).__name__}: {exc}",
        }
    result = {
        "source": source,
        "manifest": list(manifest),
        "delivery": delivery,
    }
    error = str(delivery.get("_error") or "") if delivery.get("_run_status") == "fail" else ""

    factory_dispatch = None
    if not error:
        try:
            factory_dispatch = factory_dispatch_feishu_service.sync_if_enabled(db)
        except Exception as exc:  # noqa: BLE001 - keep the order chain retryable
            db.rollback()
            factory_dispatch = {
                "ok": False,
                "errors": [f"{type(exc).__name__}: {exc}"],
            }
        result["factory_dispatch"] = factory_dispatch
        if not factory_dispatch.get("ok"):
            details = "; ".join(str(item) for item in (factory_dispatch.get("errors") or [])[:5])
            error = f"飞书系统下单表同步失败: {details or '未知原因'}"

    if error:
        result["_run_status"] = "fail"
        result["_error"] = error
        automation_pipeline_service.record_stage(
            db,
            "order_delivery",
            "recovery_closeout",
            status="fail",
            detail=error,
            artifacts=manifest,
        )
        automation_pipeline_service.resume_for_retry(db, "order_delivery")
        result["automation_pipeline"] = automation_pipeline_service.record_failure(
            db,
            "order_delivery",
            error,
            retry_slots=_retry_slots(),
            max_failures=1 + len(ORDER_RETRY_TIMES),
        )
        result["failure_event"] = automation_failure_recorder_service.record_callback_run(
            db,
            category="order",
            status="fail",
            detail=error,
            recovery_key=key,
            result_summary={"source": source, "manifest": list(manifest)},
        )
        db.commit()
        return result

    pushed = int(delivery.get("images_pushed") or 0)
    deferred = int(delivery.get("images_deferred_no_address") or 0)
    detail = (
        f"恢复来源={source}；下单图送达{pushed}张；"
        f"地址脱敏暂缓{deferred}张；工厂下单表已同步"
    )
    automation_pipeline_service.record_stage(
        db,
        "order_delivery",
        "recovery_closeout",
        status="ok",
        detail=detail,
        artifacts=manifest,
    )
    result["automation_pipeline"] = automation_pipeline_service.record_success(
        db,
        "order_delivery",
        success_detail=detail,
    )
    result["recovery_event"] = automation_failure_recorder_service.record_callback_run(
        db,
        category="order",
        status="ok",
        detail=detail,
        recovery_key=key,
        result_summary={
            "source": source,
            "manifest": list(manifest),
            "images_pushed": pushed,
            "images_deferred_no_address": deferred,
            "factory_dispatch": {
                "ok": bool((factory_dispatch or {}).get("ok")),
                "rows": int((factory_dispatch or {}).get("rows") or 0),
                "created": int((factory_dispatch or {}).get("created") or 0),
                "updated": int((factory_dispatch or {}).get("updated") or 0),
            },
        },
    )
    db.commit()
    return result
