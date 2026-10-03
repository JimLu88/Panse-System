"""Shared close-out for order delivery recovered outside the scheduler."""
from __future__ import annotations

from datetime import date, datetime
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
    order_batch_id: str | None = None,
    order_business_date: str | None = None,
    only_sub_order_nos: set[str] | None = None,
    update_current_pipeline: bool = True,
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

    key = order_batch_id or _recovery_key(source, manifest)
    is_current_business_day = update_current_pipeline and (
        not order_business_date
        or order_business_date == datetime.now().astimezone().date().isoformat()
    )
    if order_business_date and order_business_date != datetime.now().date().isoformat() and only_sub_order_nos is None:
        # Automatic historical callbacks own that business day's new lines.
        # An operator may supply the already reviewed precise catch-up scope.
        from sqlalchemy import select
        from app.models.order import Order, OrderDetail
        only_sub_order_nos = set(db.execute(select(OrderDetail.sub_order_no).join(
            Order, Order.order_no == OrderDetail.order_no,
        ).where(Order.order_date == date.fromisoformat(order_business_date),
                OrderDetail.source == "import", OrderDetail.factory_delivery_required.is_(True),
                OrderDetail.sub_order_no.isnot(None))).scalars().all())
    try:
        if only_sub_order_nos is None:
            delivery = order_sheet_archive_service.reconcile_pending_delivery(
                db, limit=ORDER_RECOVERY_PUSH_LIMIT, quiet=True,
            )
        else:
            from sqlalchemy import select
            from app.models.order import OrderDetail
            from app.services import order_line_delivery_service
            line_result = order_sheet_archive_service.reconcile_order_line_delivery(
                db, limit=ORDER_RECOVERY_PUSH_LIMIT, only_sub_order_nos=only_sub_order_nos,
            )
            delivery = {"images_pushed": 0, "line_images_pushed": int(line_result.get("pushed") or 0),
                        "line_images_failed": int(line_result.get("failed") or 0),
                        "line_failures": line_result.get("failures") or [],
                        "only_sub_order_nos": sorted(only_sub_order_nos),
                        "push_reason": line_result.get("reason")}
            delivery["images_deferred_no_address"] = sum(
                item.get("deferred") == "address_masked" for item in delivery["line_failures"])
            delivery["line_deferred_no_sku"] = [item.get("sub_order_no")
                for item in delivery["line_failures"] if item.get("deferred") == "sku_missing"]
            failures = [item for item in delivery["line_failures"]
                        if item.get("deferred") not in {"address_masked", "sku_missing"}]
            db.expire_all()
            sent_evidence = order_line_delivery_service.sent_line_evidence(db)
            rows = db.execute(select(OrderDetail).where(
                OrderDetail.sub_order_no.in_(only_sub_order_nos),
            )).scalars().all()
            receipts = [{"sub_order_no": row.sub_order_no, "factory_no": row.factory_no,
                         "message_id": row.factory_delivery_message_id}
                        for row in rows if row.factory_delivery_state == "sent"
                        and row.factory_delivery_message_id]
            confirmed = {item["sub_order_no"] for item in receipts} | set(sent_evidence)
            deferred_scope = {item.get("sub_order_no") for item in delivery["line_failures"]
                              if item.get("deferred") in {"address_masked", "sku_missing"}}
            unresolved = sorted(only_sub_order_nos - confirmed - deferred_scope)
            delivery.update(receipts=receipts, unresolved_sub_order_nos=unresolved)
            unavailable = line_result.get("reason") in ("no_chat_id", "notify_disabled")
            if failures or unavailable or unresolved:
                delivery.update(_run_status="fail", _error="scoped_line_delivery_incomplete: " +
                                str(line_result.get("reason") or failures or unresolved))
    except Exception as exc:  # noqa: BLE001 - turn callback crashes into durable evidence
        db.rollback()
        delivery = {
            "_run_status": "fail",
            "_error": f"delivery_exception: {type(exc).__name__}: {exc}",
        }
    result = {
        "source": source,
        "manifest": list(manifest),
        "order_batch_id": order_batch_id,
        "order_business_date": order_business_date,
        "delivery": delivery,
        "only_sub_order_nos": sorted(only_sub_order_nos) if only_sub_order_nos is not None else None,
    }
    error = str(delivery.get("_error") or "") if delivery.get("_run_status") == "fail" else ""

    # Projection now represents unknown production facts safely. One held image
    # must not hide unrelated rows; still retain BOTH independent failure states.
    try:
        factory_dispatch = factory_dispatch_feishu_service.sync_if_enabled(db)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        factory_dispatch = {"ok": False, "errors": [f"{type(exc).__name__}: {exc}"]}
    result["factory_dispatch"] = factory_dispatch
    if not factory_dispatch.get("ok"):
        details = "; ".join(str(item) for item in (factory_dispatch.get("errors") or [])[:5])
        error = '; '.join(x for x in [error, f"飞书系统下单表同步失败: {details or '未知原因'}"] if x)

    if error:
        result["_run_status"] = "fail"
        result["_error"] = error
        if is_current_business_day:
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
        else:
            result["automation_pipeline"] = {
                "skipped": "historical_batch_does_not_mutate_current_day_pipeline",
                "business_date": order_business_date,
            }
        result["failure_event"] = automation_failure_recorder_service.record_callback_run(
            db,
            category="order",
            status="fail",
            detail=error,
            recovery_key=key,
            result_summary={
                "source": source,
                "manifest": list(manifest),
                "order_batch_id": order_batch_id,
                "order_business_date": order_business_date,
            },
            batch_id=order_batch_id,
            business_date=order_business_date,
        )
        db.commit()
        return result

    parent_pushed = int(delivery.get("images_pushed") or 0)
    line_pushed = int(delivery.get("line_images_pushed") or 0)
    pushed = parent_pushed + line_pushed
    deferred = int(delivery.get("images_deferred_no_address") or 0)
    detail = (
        f"恢复来源={source}；下单图送达{pushed}张"
        f"（主单{parent_pushed}张、子单{line_pushed}张）；"
        f"地址脱敏暂缓{deferred}张；"
        f"SKU自动待回填{len(delivery.get('line_deferred_no_sku') or [])}张；"
        + ("工厂下单表自动同步已关闭，未同步" if factory_dispatch.get('skipped') else "工厂下单表已同步并回读")
    )
    if is_current_business_day:
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
    else:
        result["automation_pipeline"] = {
            "skipped": "historical_batch_does_not_mutate_current_day_pipeline",
            "business_date": order_business_date,
        }
    result["recovery_event"] = automation_failure_recorder_service.record_callback_run(
        db,
        category="order",
        status="ok",
        detail=detail,
        recovery_key=key,
        result_summary={
            "source": source,
            "manifest": list(manifest),
            "order_batch_id": order_batch_id,
            "order_business_date": order_business_date,
            "images_pushed": pushed,
            "parent_images_pushed": parent_pushed,
            "line_images_pushed": line_pushed,
            "images_deferred_no_address": deferred,
            "factory_dispatch": {
                "ok": bool((factory_dispatch or {}).get("ok")),
                "rows": int((factory_dispatch or {}).get("rows") or 0),
                "created": int((factory_dispatch or {}).get("created") or 0),
                "updated": int((factory_dispatch or {}).get("updated") or 0),
            },
        },
        batch_id=order_batch_id,
        business_date=order_business_date,
    )
    db.commit()
    return result
