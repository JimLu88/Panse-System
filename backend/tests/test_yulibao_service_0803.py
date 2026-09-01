from datetime import datetime
from decimal import Decimal

from app.models.finance import AccountBalance, AlipayFlow
from app.api.finance import BalanceUpsertIn, upsert_balance
from app.services import agent_ingest_service, yulibao_service


def _flow(db, *, amount, no, remark, account="企业号", ts=None):
    row = AlipayFlow(
        account=account,
        transaction_no=no,
        transaction_time=ts or datetime(2026, 8, 1, 22, 0),
        transaction_type="其它",
        amount=Decimal(str(amount)),
        reconciliation_type="internal_transfer",
        remark=remark,
    )
    db.add(row)
    db.flush()
    return row


def test_estimate_yulibao_net_asset_and_ignore_yuebao(db_session):
    _flow(db_session, amount="-7297.87", no="Y1", remark="余利宝-基金申购，支付宝转入",
          ts=datetime(2026, 7, 31, 22, 0))
    _flow(db_session, amount="-8795.92", no="Y2", remark="余利宝-基金申购，支付宝转入")
    _flow(db_session, amount="3000", no="Y3", remark="余利宝赎回，转出到支付宝")
    _flow(db_session, amount="1.23", no="Y4", remark="余利宝收益发放")
    _flow(db_session, amount="-999", no="OTHER", remark="余额宝-基金申购，支付宝转入")
    yulibao_service.set_manual_checkpoint(
        db_session,
        balance=Decimal("0"),
        as_of_date=datetime(2026, 7, 30).date(),
        note="已确认此前未启用余利宝",
    )

    result = yulibao_service.estimate_from_flows(db_session)

    assert result["ok"] is True
    assert result["balance"] == Decimal("13095.02")
    assert result["count"] == 4
    assert result["categories"] == {"purchase": 2, "redeem": 1, "profit": 1}
    assert result["as_of_date"].isoformat() == "2026-08-01"


def test_refresh_adds_standard_and_yulibao_without_double_count(monkeypatch, db_session):
    _flow(db_session, amount="-7297.87", no="Y1", remark="余利宝-基金申购，支付宝转入",
          ts=datetime(2026, 7, 31, 22, 0))
    _flow(db_session, amount="-8795.92", no="Y2", remark="余利宝-基金申购，支付宝转入")
    yulibao_service.set_manual_checkpoint(
        db_session,
        balance=Decimal("0"),
        as_of_date=datetime(2026, 7, 30).date(),
    )

    monkeypatch.setattr(
        agent_ingest_service.web_agent_service,
        "alipay_accounts",
        lambda db: [{"id": "enterprise", "name": "企业号"}],
    )
    monkeypatch.setattr(
        agent_ingest_service.web_agent_service,
        "alipay_balance",
        lambda db, account_id: {
            "ok": True,
            "balance": "759.80",
            "raw": {
                "total_amount": "759.80",
                "available_amount": "0.00",
                "freeze_amount": "759.80",
            },
        },
    )

    result = agent_ingest_service.refresh_alipay_balances(db_session)
    balances = {
        row.account_name: row
        for row in db_session.query(AccountBalance).all()
    }

    assert balances["支付宝-企业账号"].closing_balance == Decimal("759.80")
    assert balances["支付宝-企业账号-余利宝"].closing_balance == Decimal("16093.79")
    assert (
        balances["支付宝-企业账号"].closing_balance
        + balances["支付宝-企业账号-余利宝"].closing_balance
        == Decimal("16853.59")
    )
    assert balances["支付宝-企业账号-余利宝"].as_of_date.isoformat() == "2026-08-01"
    assert any(row.get("source") == "flow_estimate" for row in result)


def test_negative_estimate_does_not_overwrite_existing_balance(monkeypatch, db_session):
    _flow(db_session, amount="100", no="Y1", remark="余利宝赎回，转出到支付宝")
    yulibao_service.set_manual_checkpoint(
        db_session,
        balance=Decimal("0"),
        as_of_date=datetime(2026, 7, 31).date(),
    )
    db_session.add(AccountBalance(
        account_name="支付宝-企业账号-余利宝",
        period_year=2026,
        period_month=8,
        opening_balance=Decimal("0"),
        closing_balance=Decimal("50"),
    ))
    db_session.commit()

    monkeypatch.setattr(
        agent_ingest_service.web_agent_service,
        "alipay_accounts",
        lambda db: [{"id": "enterprise", "name": "企业号"}],
    )
    monkeypatch.setattr(
        agent_ingest_service.web_agent_service,
        "alipay_balance",
        lambda db, account_id: {"ok": False, "msg": "offline"},
    )

    result = agent_ingest_service.refresh_alipay_balances(db_session)
    stored = db_session.query(AccountBalance).filter_by(
        account_name="支付宝-企业账号-余利宝"
    ).one()

    assert stored.closing_balance == Decimal("50")
    assert any("估算为负" in row.get("error", "") for row in result)


def test_missing_checkpoint_does_not_publish_partial_balance(monkeypatch, db_session):
    _flow(db_session, amount="-80", no="Y1", remark="余利宝-基金申购，支付宝转入")
    db_session.add(AccountBalance(
        account_name="支付宝-企业账号-余利宝",
        period_year=2026,
        period_month=8,
        opening_balance=Decimal("0"),
        closing_balance=Decimal("500"),
    ))
    db_session.commit()

    monkeypatch.setattr(
        agent_ingest_service.web_agent_service,
        "alipay_accounts",
        lambda db: [{"id": "enterprise", "name": "企业号"}],
    )
    monkeypatch.setattr(
        agent_ingest_service.web_agent_service,
        "alipay_balance",
        lambda db, account_id: {"ok": False, "msg": "offline"},
    )

    result = agent_ingest_service.refresh_alipay_balances(db_session)
    estimate = yulibao_service.estimate_from_flows(db_session)
    stored = db_session.query(AccountBalance).filter_by(
        account_name="支付宝-企业账号-余利宝"
    ).one()

    assert estimate["ok"] is False
    assert estimate["reason"] == "missing_opening_checkpoint"
    assert estimate["balance"] is None
    assert estimate["count"] == 1
    assert stored.closing_balance == Decimal("500")
    assert any(
        row.get("reason") == "missing_opening_checkpoint"
        and "保留原余额" in row.get("error", "")
        for row in result
    )


def test_manual_checkpoint_only_adds_later_flows(db_session):
    _flow(
        db_session,
        amount="-999",
        no="BEFORE",
        remark="余利宝-基金申购，支付宝转入",
        ts=datetime(2026, 8, 1, 9, 0),
    )
    _flow(
        db_session,
        amount="-20",
        no="AFTER-IN",
        remark="余利宝-基金申购，支付宝转入",
        ts=datetime(2026, 8, 2, 9, 0),
    )
    _flow(
        db_session,
        amount="5",
        no="AFTER-OUT",
        remark="余利宝赎回，转出到支付宝",
        ts=datetime(2026, 8, 2, 10, 0),
    )
    yulibao_service.set_manual_checkpoint(
        db_session,
        balance=Decimal("100"),
        as_of_date=datetime(2026, 8, 1).date(),
        note="人工核对",
    )

    result = yulibao_service.estimate_from_flows(db_session)

    assert result["ok"] is True
    assert result["balance"] == Decimal("115.00")
    assert result["count"] == 2
    assert result["checkpoint"]["balance"] == Decimal("100.00")
    assert result["as_of_date"].isoformat() == "2026-08-02"


def test_manual_checkpoint_accepts_zero_and_survives_refresh(db_session):
    _flow(
        db_session,
        amount="-999",
        no="OLD",
        remark="余利宝-基金申购，支付宝转入",
        ts=datetime(2026, 8, 1, 9, 0),
    )
    yulibao_service.set_manual_checkpoint(
        db_session,
        balance=Decimal("0"),
        as_of_date=datetime(2026, 8, 2).date(),
    )

    result = yulibao_service.refresh_estimated_balance(db_session)
    stored = db_session.query(AccountBalance).filter_by(
        account_name=yulibao_service.YULIBAO_ACCOUNT_NAME,
    ).one()

    assert result["ok"] is True
    assert result["balance"] == Decimal("0.00")
    assert result["source"] == "manual_checkpoint_plus_flows"
    assert stored.closing_balance == Decimal("0.00")
    assert "人工确认" in (stored.remark or "")


def test_manual_account_edit_creates_yulibao_checkpoint(db_session):
    row = upsert_balance(
        BalanceUpsertIn(
            account_name=yulibao_service.YULIBAO_ACCOUNT_NAME,
            period_year=2026,
            period_month=8,
            as_of_date=datetime(2026, 8, 3).date(),
            closing_balance=Decimal("321.45"),
            remark="运营核对",
        ),
        db_session,
    )

    checkpoint = yulibao_service.get_manual_checkpoint(db_session)
    assert checkpoint is not None
    assert checkpoint["balance"] == Decimal("321.45")
    assert checkpoint["as_of_date"].isoformat() == "2026-08-03"
    assert checkpoint["note"] == "运营核对"
    assert row.closing_balance == Decimal("321.45")
