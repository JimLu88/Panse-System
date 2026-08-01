"""Regression: a platform-masked address is a safe defer, not a failed run."""

from app.services import agent_ingest_service as ai
from app.services import order_sheet_archive_service as oss
from app.services import scheduler


def test_daily_address_defer_is_success_not_failure(db_session, monkeypatch):
    monkeypatch.setattr(ai, "order_data_fresh", lambda db, **kwargs: True)
    monkeypatch.setattr(ai, "latest_order_pull_result", lambda db: {"changes": None})
    monkeypatch.setattr(
        oss,
        "push_daily",
        lambda db: {
            "images_pushed": 2,
            "images_failed": 0,
            "images_remaining": 1,
            "held_no_sku": [],
            "held_no_address": ["ORDER-MASKED-1"],
            "remote_feishu_failed": [],
            "push_reason": None,
        },
    )
    monkeypatch.setattr(scheduler, "_sync_factory_dispatch_after_orders", lambda *a, **k: {})
    monkeypatch.setattr(
        scheduler,
        "_record_pipeline_result",
        lambda db, pipeline, result, **kwargs: result,
    )

    result = scheduler._job_order_sheets_daily(db_session)

    assert result.get("_run_status") != "fail"
    assert result["deferred_status"] == "address_masked"
    assert "自动补推" in result["_success_message"]
