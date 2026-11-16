"""Resume only the order pipeline already waiting when shop login was selected."""
import json
from sqlalchemy import select
from app.models.settings import SystemSetting
from app.services import automation_pipeline_service as pipeline, settings_service


def pending_order(db, platform):
    if platform != "shop":
        return None
    state = pipeline.get_pipeline(db, "order_delivery")
    if (state.get("success") or not state.get("waiting_input")
            or not state.get("last_attempt_at")
            or pipeline.classify_failure(state.get("last_error") or "")["reason"] != "login_required"):
        return None
    return {key: state.get(key) for key in ("date", "run_key", "last_attempt_at")}


def queue_existing_order(db, *, platform, key, job, expected):
    from app.services import platform_scan_service as scan, scheduler
    if not expected or not job or pending_order(db, platform) != expected:
        return False
    # Preserve the ordinary order window and unknown-export/freshness guards.
    if not 18 <= scheduler._now_hour() < 23:
        return False
    row = db.execute(select(SystemSetting).where(SystemSetting.key == key)
                     .with_for_update().execution_options(populate_existing=True)).scalar_one_or_none()
    value = json.loads(row.value_plain or "{}") if row else {}
    if (value.get("state") != "success" or value.get("platform") != "shop"
            or value.get("job") != job or value.get("order_resume_requested")):
        return False
    active = scan._load(db, scan.ACTIVE_KEY)
    if active.get("request_key") != key or active.get("job") != job:
        return False
    # A crash after this claim still leaves the original scheduled catchup.
    value["order_resume_requested"] = expected
    settings_service.set_value(db, key, json.dumps(value))
    settings_service.set_value(db, scan.ACTIVE_KEY, json.dumps(value))
    db.commit()
    return _dispatch(expected, key, job)


def _dispatch(expected, key, job):
    from app.services import scheduler
    # Same scheduler job claim/logging; recheck the original run at execution,
    # not only when login finished. No new export path or independent loop.
    scheduler._run_with_logging(
        "pull_catchup_30min", "店铺登录后续跑原订单",
        lambda db: _continue_original(db, expected, key, job),
    )
    return True


def _continue_original(db, expected, key, job):
    from app.services import platform_scan_service as scan, scheduler
    value = scan._load(db, key)
    if (pending_order(db, "shop") != expected or value.get("state") != "success"
            or value.get("job") != job or value.get("order_resume_requested") != expected):
        return {"skipped": "original_login_wait_changed", "_run_status": "skipped"}
    return scheduler._job_pull_catchup(db)
