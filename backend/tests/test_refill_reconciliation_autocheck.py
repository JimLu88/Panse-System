"""评价补单更新后的自动回填、重算和误报销账。"""
import base64
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

from fastapi import BackgroundTasks
from sqlalchemy import select

from app.api import importer
from app.models.exception import DataException
from app.models.finance import AlipayFlow, RefillRecord
from app.models.order import Order
from app.services import order_sync_service, reconciliation_service


def test_late_order_repair_makes_refill_transfer_balance_and_closes_old_diff(db_session):
    order_no = "3310000000000000101"
    db_session.add_all([
        Order(
            platform="淘宝", order_no=order_no, order_date=date(2026, 9, 15),
            paid_amount=Decimal("23.00"), status="signed", is_refill=False,
        ),
        RefillRecord(
            order_no=order_no, refill_date=date(2026, 9, 17), order_amount=None,
            commission=Decimal("15.00"), remark=order_sync_service.REVIEW_SYNC_REMARK,
        ),
        AlipayFlow(
            account="主力号", transaction_no="refill-order-101",
            transaction_time=datetime(2026, 9, 20, 12), amount=Decimal("-23.00"),
            counterparty="晶晶(**晶)", remark="9.15-b流水",
        ),
        AlipayFlow(
            account="主力号", transaction_no="refill-commission-101",
            transaction_time=datetime(2026, 9, 20, 12), amount=Decimal("-15.00"),
            counterparty="晶晶(**晶)", remark="9.15-Y",
        ),
    ])
    db_session.flush()

    before = reconciliation_service.run_refill_transfer(db_session, record_exceptions=True)
    assert any(d.key == "2026-09-15-订单额" and d.severity not in ("ok", "not_available")
               for d in before.diffs)
    old = db_session.scalar(select(DataException).where(
        DataException.source_pk == "refill_transfer:2026-09-15-订单额",
        DataException.status == "open",
    ))
    assert old is not None

    order_sync_service.repair_review_refill_records(db_session)
    after = reconciliation_service.run_refill_transfer(db_session, record_exceptions=True)
    assert next(d for d in after.diffs if d.key == "2026-09-15-订单额").severity == "ok"
    assert next(d for d in after.diffs if d.key == "2026-09-15-佣金").severity == "ok"
    reconciliation_service._autoclose_resolved_diffs(db_session, {"refill_transfer": after})
    assert old.status == "resolved"


def test_confirmed_import_schedules_finance_recheck_but_dry_run_does_not(db_session, monkeypatch):
    calls = []
    monkeypatch.setattr(importer, "_schedule_finance_recheck", calls.append)
    report = SimpleNamespace(
        entity_type="alipay_flow", sheet_name="sheet", total_rows=1,
        inserted_parents=1, inserted_children=0, skipped_rows=0,
        matched_lines=0, auto_created_suppliers=[], errors=[], warnings=[],
        conflicts=[], unmapped_columns=[],
    )
    monkeypatch.setattr(importer.excel_importer, "commit_sheet", lambda *a, **kw: report)
    payload = importer.CommitIn(
        file_b64=base64.b64encode(b"test").decode(), sheet_name="sheet",
        entity_type="alipay_flow", mapping={},
    )

    importer.commit_import(payload, db_session, None)
    importer.commit_import(payload.model_copy(update={"dry_run": True}), db_session, None)

    assert calls == ["importer:alipay_flow"]


def test_smart_order_import_schedules_one_debounced_recheck(db_session, monkeypatch):
    calls = []
    monkeypatch.setattr(importer, "_schedule_finance_recheck", calls.append)
    from app.services import smart_import_service
    monkeypatch.setattr(smart_import_service, "smart_commit", lambda *a, **kw: [
        {"sheet_name": "订单", "entity_type": "order", "inserted_parents": 5},
        {"sheet_name": "试跑", "entity_type": "refill_record", "inserted_parents": 1},
    ])
    payload = importer.SmartCommitIn(
        file_b64=base64.b64encode(b"test").decode(),
        plan=[
            importer.SmartCommitItem(sheet_name="订单", entity_type="order", mapping={}),
            importer.SmartCommitItem(
                sheet_name="试跑", entity_type="refill_record", mapping={}, dry_run=True,
            ),
        ],
    )

    importer.smart_commit(payload, BackgroundTasks(), db_session, None)

    assert calls == ["importer:smart-commit"]
