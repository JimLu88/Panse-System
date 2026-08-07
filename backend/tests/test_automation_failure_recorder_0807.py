from datetime import date, datetime, timedelta, timezone

from app.models.scheduled_job import ScheduledJobRun
from app.services import automation_failure_recorder_service as recorder


def _run(job_id, status, started_at, *, error=None, summary=None):
    return ScheduledJobRun(
        job_id=job_id,
        job_label=job_id,
        status=status,
        error=error,
        result_summary=summary,
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=2),
    )


def test_records_every_failure_and_marks_later_recovery(db_session):
    started = datetime(2026, 8, 7, 2, 0, tzinfo=timezone.utc)
    db_session.add_all([
        _run("daily_0630_web_agent", "fail", started, error="PC offline"),
        _run("daily_0630_web_agent", "fail", started + timedelta(minutes=30), error="password pending"),
        _run("pull_catchup_30min", "ok", started + timedelta(hours=1)),
        _run("unrelated_job", "fail", started, error="do not show"),
    ])
    db_session.commit()

    result = recorder.list_failure_events(db_session, on=date(2026, 8, 7))

    assert result["total"] == 2
    assert result["open_count"] == 0
    assert result["by_category"]["order"] == 2
    assert [x["attempt_no"] for x in reversed(result["items"])] == [1, 2]
    assert all(x["state"] == "recovered" for x in result["items"])
    assert all(x["recovered_at"] for x in result["items"])


def test_extracts_retry_final_waiting_and_source_failures(db_session):
    started = datetime(2026, 8, 7, 13, 0, tzinfo=timezone.utc)
    db_session.add(_run(
        "daily_2030_finance_agent",
        "fail",
        started,
        error="主力号流水未完成",
        summary={
            "tasks": [{"task": "alipay_main_flow", "status": "error", "error": "session expired"}],
            "flow_pipeline": {
                "next_retry_at": "2026-08-07T21:30:00+08:00",
                "waiting_input": True,
                "final": False,
            },
        },
    ))
    db_session.commit()

    result = recorder.list_failure_events(
        db_session, on=date(2026, 8, 7), category="finance",
    )

    assert result["total"] == 1
    event = result["items"][0]
    assert event["state"] == "waiting_input"
    assert event["next_retry_at"] == "2026-08-07T21:30:00+08:00"
    assert event["source_failures"] == [{
        "task": "alipay_main_flow",
        "status": "error",
        "reason": "session expired",
    }]


def test_uses_beijing_calendar_day(db_session):
    db_session.add_all([
        _run("campaign_auto_execute", "fail", datetime(2026, 8, 6, 15, 59, tzinfo=timezone.utc)),
        _run("campaign_auto_execute", "fail", datetime(2026, 8, 6, 16, 1, tzinfo=timezone.utc)),
    ])
    db_session.commit()

    result = recorder.list_failure_events(db_session, on=date(2026, 8, 7), category="campaign")

    assert result["total"] == 1
    assert result["items"][0]["started_at"].startswith("2026-08-07T00:01")


def test_callback_success_closes_scheduler_failures(db_session):
    started = datetime(2026, 8, 7, 10, 0, tzinfo=timezone.utc)
    db_session.add(_run(
        "daily_1810_order_sheets",
        "fail",
        started,
        error="shipping password pending",
    ))
    recorder.record_callback_run(
        db_session,
        category="order",
        status="ok",
        detail="password callback completed delivery",
        recovery_key="manifest-1",
        now=started + timedelta(minutes=5),
    )
    db_session.commit()

    result = recorder.list_failure_events(db_session, on=date(2026, 8, 7))

    assert result["total"] == 1
    assert result["open_count"] == 0
    assert result["items"][0]["state"] == "recovered"
