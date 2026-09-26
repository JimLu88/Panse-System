"""评价补单更新后的自动回填、重算和误报销账。"""
import base64
from datetime import date, datetime, timezone
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


def test_alternate_payee_pair_matches_previous_business_day_and_reclassifies(db_session):
    business_date = date(2026, 9, 14)
    payment_time = datetime(2026, 9, 14, 16, 17, tzinfo=timezone.utc)  # 北京时间次日 00:17
    db_session.add_all([
        RefillRecord(
            order_no="ALT-REGULAR", refill_date=business_date,
            order_amount=Decimal("104.67"), commission=Decimal("75.00"),
        ),
        RefillRecord(
            order_no="ALT-STORE", refill_date=business_date,
            order_amount=Decimal("69.33"), commission=Decimal("45.00"),
        ),
        AlipayFlow(
            account="主力号", transaction_no="ALT-XJJ-B", transaction_time=payment_time,
            amount=Decimal("-104.67"), counterparty="徐晶晶", remark="9.14-b流水",
        ),
        AlipayFlow(
            account="主力号", transaction_no="ALT-XJJ-Y", transaction_time=payment_time,
            amount=Decimal("-75.00"), counterparty="徐晶晶", remark="9.14-Y",
        ),
        AlipayFlow(
            account="主力号", transaction_no="ALT-STORE-B", transaction_time=payment_time,
            amount=Decimal("-69.33"), counterparty="俩仟万设计理念店",
            remark="经营码交易", reconciliation_type="boguan_payment",
        ),
        AlipayFlow(
            account="主力号", transaction_no="ALT-STORE-Y", transaction_time=payment_time,
            amount=Decimal("-45.00"), counterparty="俩仟万设计理念店",
            remark="经营码交易", reconciliation_type="boguan_payment",
        ),
    ])
    db_session.flush()

    result = reconciliation_service.run_refill_transfer(db_session, record_exceptions=False)
    by_key = {row.key: row for row in result.diffs}
    assert by_key["2026-09-14-订单额"].severity == "ok"
    assert by_key["2026-09-14-订单额"].actual == Decimal("174.00")
    assert by_key["2026-09-14-佣金"].severity == "ok"
    assert by_key["2026-09-14-佣金"].actual == Decimal("120.00")

    repair = reconciliation_service.reclassify_alternate_refill_transfers(db_session)
    assert repair == {
        "matched": 2, "updated": 2, "business_days": ["2026-09-14"],
    }
    store_flows = db_session.scalars(select(AlipayFlow).where(
        AlipayFlow.counterparty == "俩仟万设计理念店",
    )).all()
    assert {flow.reconciliation_type for flow in store_flows} == {"refill_transfer"}

    monthly = reconciliation_service.run_refill_commission_payout(
        db_session, period_start=business_date, period_end=business_date,
        record_exceptions=False,
    )
    september = next(row for row in monthly.diffs if row.key == "2026-09")
    assert september.expected == Decimal("120.00")
    assert september.actual == Decimal("120.00")
    assert september.severity == "ok"


def test_alternate_payee_requires_exact_principal_and_commission_pair(db_session):
    business_date = date(2026, 9, 14)
    payment_time = datetime(2026, 9, 14, 16, 17, tzinfo=timezone.utc)
    db_session.add_all([
        RefillRecord(
            order_no="ALT-GUARD", refill_date=business_date,
            order_amount=Decimal("69.33"), commission=Decimal("45.00"),
        ),
        AlipayFlow(
            account="主力号", transaction_no="ALT-GUARD-B", transaction_time=payment_time,
            amount=Decimal("-70.00"), counterparty="俩仟万设计理念店",
            remark="经营码交易", reconciliation_type="boguan_payment",
        ),
        AlipayFlow(
            account="主力号", transaction_no="ALT-GUARD-Y", transaction_time=payment_time,
            amount=Decimal("-45.00"), counterparty="俩仟万设计理念店",
            remark="经营码交易", reconciliation_type="boguan_payment",
        ),
    ])
    db_session.flush()

    repair = reconciliation_service.reclassify_alternate_refill_transfers(db_session)
    assert repair == {"matched": 0, "updated": 0, "business_days": []}
    types = db_session.scalars(select(AlipayFlow.reconciliation_type)).all()
    assert types == ["boguan_payment", "boguan_payment"]


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
