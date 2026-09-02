from datetime import date
from decimal import Decimal

from app.api.factory_orders import _monthly_summary, _row
from app.models.order import FactoryOrder


def _factory_order(no: str, *, day: date, expected=None, actual=None, status="unpaid", note=None, flow=None):
    return FactoryOrder(
        factory_order_no=no,
        order_date=day,
        expected_amount=Decimal(str(expected)) if expected is not None else None,
        factory_bill_amount=Decimal(str(actual)) if actual is not None else None,
        payment_status=status,
        unpaid_reason_note=note,
        alipay_flow_no=flow,
    )


def test_unpaid_reason_uses_verifiable_fields():
    missing = _row(_factory_order("F1", day=date(2026, 6, 1), expected=100))
    assert missing["unpaid_reason"] == "未导入或未匹配工厂账单，暂按推算成本待付"

    billed = _row(_factory_order("F2", day=date(2026, 6, 2), expected=100, actual=90))
    assert billed["unpaid_reason"] == "已有工厂账单，尚未匹配付款流水或月结销账"

    stale = _row(_factory_order("F3", day=date(2026, 6, 3), expected=100, actual=90, flow="A1"))
    assert stale["unpaid_reason"] == "已有付款信息但状态仍为未付，需核销支付状态"

    paid = _row(_factory_order("F4", day=date(2026, 6, 4), expected=100, actual=90, status="paid"))
    assert paid["unpaid_reason"] is None


def test_monthly_summary_keeps_paid_unpaid_and_investigation_counts():
    rows = [
        _row(_factory_order("F1", day=date(2026, 6, 1), expected=100)),
        _row(_factory_order("F2", day=date(2026, 6, 2), expected=200, actual=180, note="材料抵扣，待确认")),
        _row(_factory_order("F3", day=date(2026, 6, 3), expected=300, actual=280, status="paid")),
        _row(_factory_order("F4", day=date(2026, 7, 1), expected=50)),
    ]

    result = _monthly_summary(rows)
    june = next(row for row in result if row["month"] == "2026-06")
    assert june["count"] == 3
    assert june["paid_count"] == 1
    assert june["paid_sum"] == 280
    assert june["unpaid_count"] == 2
    assert june["unpaid_sum"] == 280
    assert june["missing_bill_count"] == 1
    assert june["unresolved_count"] == 1
