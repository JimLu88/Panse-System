"""工厂预付款待抵扣：现金流加项、月结自动抵扣、撤销恢复、人工归零。"""
from datetime import date
from decimal import Decimal

from app.models.factory_settlement import DEFAULT_WOOD_SUPPLIER as SUP
from app.models.order import FactoryOrder
from app.services import cash_flow_service
from app.services import factory_advance_service as fas
from app.services import factory_settlement_service as fss


def _order(db, no: str, amount: str, month: str = "2026-07") -> None:
    db.add(FactoryOrder(
        factory_order_no=no,
        factory_name=SUP,
        factory_bill_amount=Decimal(amount),
        payment_status="unpaid",
        settlement_month=month,
        order_date=date(2026, 7, 1),
    ))
    db.flush()


def test_advance_is_cashflow_asset_and_can_be_manually_zeroed(db_session):
    db = db_session
    fas.set_manual(
        db,
        balance=Decimal("30000"),
        target_month="2026-07",
        note="7月15日多转，待7月账单抵扣",
        by="tester",
    )

    summary = cash_flow_service.compute_summary(db)
    row = next(x for x in summary["additions"] if x["key"] == "factory_advance")
    assert row["amount"] == Decimal("30000")
    assert summary["manual"]["factory_advance_balance"] == "30000.00"

    state = fas.set_manual(
        db,
        balance=Decimal("0"),
        target_month="2026-07",
        note="已由工厂中途抵完",
        by="tester",
    )
    assert state["status"] == "settled"
    assert state["balance"] == "0.00"
    assert state["history"][0]["before"] == "30000.00"
    assert state["history"][0]["after"] == "0.00"
    assert next(
        x for x in cash_flow_service.compute_summary(db)["additions"]
        if x["key"] == "factory_advance"
    )["amount"] == Decimal("0")


def test_settlement_applies_advance_and_reverse_restores_it(db_session):
    db = db_session
    _order(db, "ADV-1", "20000")
    _order(db, "ADV-2", "18490")
    fas.set_manual(
        db,
        balance=Decimal("30000"),
        target_month="2026-07",
        note="待7月抵扣",
        by="tester",
    )

    result = fss.settle_month(db, month="2026-07", by="tester")
    assert result["billed_total"] == Decimal("38490.00")
    assert result["advance_used"] == Decimal("30000.00")
    assert result["net_cash_payable"] == Decimal("8490.00")
    assert fas.get_state(db)["balance"] == "0.00"
    payment = fss.list_payments(db)[0]
    assert payment["advance_used"] == "30000.00"

    reversed_result = fss.reverse_settlement(db, result["payment_id"], by="tester")
    assert reversed_result["reverted"] == 2
    assert reversed_result["advance_restored"] == Decimal("30000.00")
    assert fas.get_state(db)["balance"] == "30000.00"
    assert fss.list_payments(db)[0]["advance_used"] == "0.00"


def test_advance_only_applies_to_target_month(db_session):
    db = db_session
    _order(db, "ADV-JUNE", "5000", month="2026-06")
    fas.set_manual(
        db,
        balance=Decimal("30000"),
        target_month="2026-07",
        note="只抵7月",
        by="tester",
    )

    result = fss.settle_month(db, month="2026-06", by="tester")
    assert result["advance_used"] == Decimal("0.00")
    assert result["net_cash_payable"] == Decimal("5000.00")
    assert fas.get_state(db)["balance"] == "30000.00"
