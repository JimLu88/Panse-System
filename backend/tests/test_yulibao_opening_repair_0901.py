from datetime import datetime
from decimal import Decimal

import pytest

from app.models.field_change import FieldChange
from app.models.finance import AccountBalance, AlipayFlow
from app.services import settings_service, yulibao_opening_repair_service as repair_service
from app.services import yulibao_service


def _flow(
    db,
    *,
    no: str,
    ts: datetime,
    amount: str,
    balance: str,
    remark: str,
    account: str = "企业号",
    reconciliation_type=None,
):
    row = AlipayFlow(
        account=account,
        transaction_no=no,
        transaction_time=ts,
        transaction_type="其它",
        amount=Decimal(amount),
        balance=Decimal(balance),
        reconciliation_status="open",
        reconciliation_type=reconciliation_type,
        remark=remark,
    )
    db.add(row)
    db.flush()
    return row


def _production_scope(db, *, extra=False):
    rows = [
        _flow(
            db,
            no="BANK-1",
            ts=datetime(2026, 7, 31, 6, 47, 14),
            amount="-100000.00",
            balance="261447.82",
            remark="转出到网商银行",
        ),
        _flow(
            db,
            no="BANK-2",
            ts=datetime(2026, 7, 31, 6, 47, 48),
            amount="-200000.00",
            balance="61447.82",
            remark="转出到网商银行",
        ),
        _flow(
            db,
            no="BANK-3",
            ts=datetime(2026, 7, 31, 6, 48, 5),
            amount="-60766.28",
            balance="681.54",
            remark="转出到网商银行",
        ),
    ]
    _flow(
        db,
        no="YL-OPEN",
        ts=datetime(2026, 7, 31, 22, 0, 17),
        amount="-7297.87",
        balance="698.04",
        remark="余利宝-基金申购，支付宝转入",
        reconciliation_type="internal_transfer",
    )
    _flow(
        db,
        no="YL-LATER",
        ts=datetime(2026, 8, 30, 22, 0, 23),
        amount="-73824.90",
        balance="827.25",
        remark="余利宝-基金申购，支付宝转入",
        reconciliation_type="internal_transfer",
    )
    if extra:
        _flow(
            db,
            no="BANK-EXTRA",
            ts=datetime(2026, 8, 1, 12, 0, 0),
            amount="-1.00",
            balance="680.54",
            remark="转出到网商银行",
        )
    return rows


def test_exact_repair_is_audited_and_idempotent(db_session):
    transfers = _production_scope(db_session)

    dry_run = repair_service.repair(db_session, apply=False)
    assert dry_run["dry_run"] is True
    assert dry_run["transfer_total"] == "360766.28"
    assert all(row.reconciliation_type is None for row in transfers)
    assert yulibao_service.get_manual_checkpoint(db_session) is None

    applied = repair_service.repair(db_session, apply=True)
    db_session.commit()

    checkpoint = yulibao_service.get_manual_checkpoint(db_session)
    stored = db_session.query(AccountBalance).filter_by(
        account_name=yulibao_service.YULIBAO_ACCOUNT_NAME,
    ).one()
    audit = db_session.query(FieldChange).filter_by(
        source="repair_0901",
    ).all()

    assert applied["applied"] is True
    assert checkpoint["balance"] == Decimal("368064.15")
    assert checkpoint["as_of_date"].isoformat() == "2026-07-31"
    assert stored.closing_balance == Decimal("441889.05")
    assert stored.as_of_date.isoformat() == "2026-08-30"
    assert all(row.reconciliation_type == "internal_transfer" for row in transfers)
    assert len(audit) == 4
    assert settings_service.get(
        db_session, repair_service.REPAIR_RECEIPT_KEY, env_fallback=False,
    )

    reused = repair_service.repair(db_session, apply=True)
    db_session.commit()
    assert reused["applied"] is False
    assert reused["reused"] is True
    assert db_session.query(FieldChange).filter_by(
        source="repair_0901",
    ).count() == 4


def test_repair_fails_closed_when_any_bank_row_is_extra(db_session):
    transfers = _production_scope(db_session, extra=True)

    with pytest.raises(repair_service.RepairScopeError, match="批准范围不一致"):
        repair_service.repair(db_session, apply=True)

    assert all(row.reconciliation_type is None for row in transfers)
    assert yulibao_service.get_manual_checkpoint(db_session) is None


def test_repair_fails_closed_on_conflicting_checkpoint(db_session):
    transfers = _production_scope(db_session)
    yulibao_service.set_manual_checkpoint(
        db_session,
        balance=Decimal("1.00"),
        as_of_date=datetime(2026, 7, 31).date(),
        note="冲突值",
    )

    with pytest.raises(repair_service.RepairScopeError, match="基准.*冲突"):
        repair_service.repair(db_session, apply=True)

    assert all(row.reconciliation_type is None for row in transfers)


def test_unrelated_bank_transfer_is_not_yulibao_evidence(db_session):
    _flow(
        db_session,
        no="UNRELATED",
        ts=datetime(2026, 8, 2, 10, 0, 0),
        amount="-100.00",
        balance="500.00",
        remark="转出到网商银行",
        reconciliation_type="internal_transfer",
    )
    yulibao_service.set_manual_checkpoint(
        db_session,
        balance=Decimal("20.00"),
        as_of_date=datetime(2026, 8, 1).date(),
    )

    estimate = yulibao_service.estimate_from_flows(db_session)

    assert estimate["ok"] is True
    assert estimate["balance"] == Decimal("20.00")
    assert estimate["count"] == 0
