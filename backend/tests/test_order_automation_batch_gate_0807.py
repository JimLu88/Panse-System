"""Regression gates for the order pull -> image -> Feishu delivery chain."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook

from app.models.import_file import ImportedFile
from app.models.order import Order
from app.models.scheduled_job import ScheduledJobRun
from app.services import agent_ingest_service as ingest
from app.services import automation_failure_recorder_service as failure_recorder
from app.services import order_sheet_archive_service as sheets
from app.services import taobao_order_import


def _xlsx_with_sheet(title: str) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = title
    ws.append(["placeholder"])
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _artifact(db, name: str, role: str, batch_id: str, *, status: str = "imported"):
    db.add(ImportedFile(
        kind="taobao",
        original_filename=name,
        stored_path=f"D:/Temp/{batch_id}/{name}",
        file_hash=f"{batch_id}-{name}",
        source="agent",
        row_summary={
            "agent_status": status,
            "agent_report_role": role,
            "automation_batch_id": batch_id,
        },
    ))
    db.flush()


def _scheduled_run(job_id: str, status: str, started_at: datetime, *, batch_id: str):
    return ScheduledJobRun(
        job_id=job_id,
        job_label=job_id,
        status=status,
        error="failed" if status == "fail" else None,
        result_summary={
            "order_batch_id": batch_id,
            "order_business_date": "2026-08-07",
        },
        started_at=started_at,
        completed_at=started_at + timedelta(seconds=2),
    )


def test_content_role_detector_distinguishes_three_reports():
    assert taobao_order_import.detect_report_role(
        "a.xlsx", _xlsx_with_sheet("订单报表"),
    ) == "orders"
    assert taobao_order_import.detect_report_role(
        "b.xlsx", _xlsx_with_sheet("销售明细"),
    ) == "sales_detail"
    assert taobao_order_import.detect_report_role(
        "c.xlsx", bytes.fromhex("d0cf11e0a1b11ae1") + b"encrypted",
    ) == "shipping"


def test_batch_gate_requires_exactly_one_of_each_report(db_session):
    batch_id = "orders-20260807-exact"
    names = ["orders.xlsx", "items.xlsx", "shipping.xlsx"]
    for name, role in zip(names, ("orders", "sales_detail", "shipping")):
        _artifact(db_session, name, role, batch_id)

    valid = ingest.validate_order_pull_artifact_roles(
        db_session, names, order_batch_id=batch_id,
    )
    assert valid["ok"] is True
    assert valid["missing_roles"] == []
    assert valid["duplicate_roles"] == {}

    duplicate = ingest.validate_order_pull_artifact_roles(
        db_session,
        ["orders.xlsx", "items.xlsx", "shipping.xlsx"],
        declared_roles={
            "orders.xlsx": "orders",
            "items.xlsx": "sales_detail",
            "shipping.xlsx": "sales_detail",
        },
        order_batch_id="different-batch-with-no-persisted-rows",
    )
    assert duplicate["ok"] is False
    assert duplicate["missing_roles"] == ["shipping"]
    assert duplicate["duplicate_roles"] == {
        "sales_detail": ["items.xlsx", "shipping.xlsx"],
    }


def test_freshness_gate_rejects_artifacts_from_another_batch(db_session):
    target = date(2026, 8, 7)
    names = ["orders.xlsx", "items.xlsx", "shipping.xlsx"]
    roles = dict(zip(names, ("orders", "sales_detail", "shipping")))
    for name, role in roles.items():
        _artifact(db_session, name, role, "batch-A")
    ingest._save_json(db_session, ingest.KEY_STATE, {
        "taobao_orders_complete": "2026-08-07T18:30:00",
        "taobao_orders_complete_business_date": target.isoformat(),
        "taobao_orders_complete_batch_id": "batch-B",
        "taobao_orders_complete_artifacts": names,
        "taobao_orders_complete_artifact_roles": roles,
        "taobao_orders_complete_legacy_evidence": False,
    })
    db_session.commit()

    assert ingest.order_data_fresh(
        db_session, on=target, not_before_hour=0,
    ) is False


def test_different_batch_success_does_not_close_failure(db_session):
    started = datetime(2026, 8, 7, 10, 0, tzinfo=timezone.utc)
    db_session.add_all([
        _scheduled_run("daily_0630_web_agent", "fail", started, batch_id="batch-A"),
        _scheduled_run(
            "pull_catchup_30min", "ok", started + timedelta(minutes=10), batch_id="batch-B",
        ),
    ])
    db_session.commit()

    result = failure_recorder.list_failure_events(db_session, on=date(2026, 8, 7))
    assert result["total"] == 1
    assert result["open_count"] == 1
    assert result["items"][0]["state"] == "open"


def test_same_batch_callback_after_midnight_closes_failure(db_session, monkeypatch):
    failure_at = datetime(2026, 8, 7, 15, 50, tzinfo=timezone.utc)  # 23:50 Beijing
    recovery_at = datetime(2026, 8, 7, 16, 5, tzinfo=timezone.utc)  # 00:05 next day
    db_session.add(_scheduled_run(
        "daily_0630_web_agent", "fail", failure_at, batch_id="batch-cross-midnight",
    ))
    failure_recorder.record_callback_run(
        db_session,
        category="order",
        status="ok",
        detail="password recovery completed",
        recovery_key="batch-cross-midnight",
        batch_id="batch-cross-midnight",
        business_date="2026-08-07",
        now=recovery_at,
    )
    db_session.commit()

    class _AfterRecovery(datetime):
        @classmethod
        def now(cls, tz=None):
            value = recovery_at + timedelta(minutes=1)
            return value if tz is None else value.astimezone(tz)

    monkeypatch.setattr(failure_recorder, "datetime", _AfterRecovery)
    result = failure_recorder.list_failure_events(db_session, on=date(2026, 8, 7))
    assert result["total"] == 1
    assert result["open_count"] == 0
    assert result["items"][0]["state"] == "recovered"
    assert result["items"][0]["batch_id"] == "batch-cross-midnight"


def test_image_generation_exception_is_returned_with_order_number(db_session, monkeypatch):
    order = Order(
        platform="淘宝",
        order_no="IMAGE-FAIL-1",
        qty=1,
        product_name="测试产品",
        sku="标准款",
        order_date=date(2026, 8, 7),
        status="paid",
        paid_amount=Decimal("1000"),
    )
    db_session.add(order)
    db_session.commit()
    monkeypatch.setattr(
        sheets.factory_sheet,
        "build",
        lambda db, order_id: (_ for _ in ()).throw(RuntimeError("render source missing")),
    )

    result = sheets.generate_pending(db_session)
    assert result["generation_failed"] == 1
    assert result["generation_failed_order_nos"] == ["IMAGE-FAIL-1"]
    assert "render source missing" in result["generation_failures"][0]["error"]


def test_delivery_closeout_fails_when_any_image_generation_failed(db_session, monkeypatch):
    monkeypatch.setattr(sheets, "void_remote_pushed", lambda db: {
        "voided_remote": 0,
        "remote_transitions": [],
        "feishu_notified": 0,
        "feishu_failed": [],
    })
    monkeypatch.setattr(sheets, "repush_activated", lambda db: {"repushed": 0})
    monkeypatch.setattr(sheets, "assign_remote_seqs", lambda db: 0)
    monkeypatch.setattr(sheets, "generate_pending", lambda db: {
        "generated": 0,
        "generation_failed": 1,
        "generation_failed_order_nos": ["IMAGE-FAIL-2"],
        "generation_failures": [{
            "order_no": "IMAGE-FAIL-2",
            "error": "RuntimeError: canvas failed",
        }],
    })
    monkeypatch.setattr(sheets, "push_pending_images", lambda db, **kwargs: {
        "pushed": 0,
        "failed": 0,
        "remaining": 0,
        "order_nos": [],
        "failed_order_nos": [],
        "held_no_sku": [],
        "held_no_address": [],
        "held_remote": [],
        "delivery_uncertain": [],
        "skipped_sample": [],
        "skipped_topup": [],
    })

    result = sheets.reconcile_pending_delivery(db_session)
    assert result["_run_status"] == "fail"
    assert "IMAGE-FAIL-2" in result["_error"]
