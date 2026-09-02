from datetime import date
from decimal import Decimal

from app.models.order import FactoryOrder
from app.services.factory_bill_import_service import BillLine, _apply, parse_sheet_rows


def test_order_no_2_header_is_parsed_as_an_extra_order():
    rows = [
        ["订单号", "订单号2", "详情", "数量", "价格"],
        ["5120000000000000001", "5120000000000000002", "餐边柜", 1, 1250],
    ]

    lines, skipped, subtotals = parse_sheet_rows(rows)

    assert skipped == []
    assert subtotals == []
    assert len(lines) == 1
    assert lines[0].order_no == "5120000000000000001"
    assert lines[0].extra_nos == ["5120000000000000002"]


def test_extra_order_is_linked_and_excluded_from_separate_factory_cost(db_session):
    primary = FactoryOrder(
        factory_order_no="F-TOPUP-1",
        platform_order_no="5120000000000000001",
        order_date=date(2026, 8, 1),
        payment_status="unpaid",
    )
    topup = FactoryOrder(
        factory_order_no="F-TOPUP-2",
        platform_order_no="5120000000000000002",
        order_date=date(2026, 8, 1),
        expected_amount=Decimal("300"),
        payment_status="unpaid",
    )
    db_session.add_all([primary, topup])
    db_session.flush()

    result = _apply(db_session, [BillLine(
        order_no=primary.platform_order_no,
        extra_nos=[topup.platform_order_no],
        price=Decimal("1250"),
        price_raw=1250,
    )])

    assert result["updated"] == 1
    assert result["topup_linked"] == 1
    assert primary.factory_bill_amount == Decimal("1250")
    assert topup.factory_cost_type == "same_order_topup"
    assert topup.related_primary_order_no == primary.platform_order_no
    assert topup.factory_bill_amount == Decimal("0")

    # 重复导入不重复记关联，也不重复更新。
    again = _apply(db_session, [BillLine(
        order_no=primary.platform_order_no,
        extra_nos=[topup.platform_order_no],
        price=Decimal("1250"),
        price_raw=1250,
    )])
    assert again["unchanged"] == 1
    assert again["topup_linked"] == 0


def test_same_order_multiple_bill_rows_are_summed(db_session):
    order = FactoryOrder(
        factory_order_no="F-MULTI",
        platform_order_no="3304028352501005156",
        order_date=date(2026, 6, 1),
        payment_status="paid",
    )
    db_session.add(order)
    db_session.flush()

    result = _apply(db_session, [
        BillLine(order_no=order.platform_order_no, price=Decimal("460"), price_raw=460),
        BillLine(order_no=order.platform_order_no, price=Decimal("400"), price_raw=400),
    ])

    assert result["updated"] == 1
    assert order.factory_bill_amount == Decimal("860")


def test_rows_after_bill_balance_marker_are_not_imported():
    rows = [
        ["订单号1", "订单号2", "详情", "数量", "价格"],
        ["5120000000000000001", None, "本期订单", 1, 1200],
        [None, None, None, None, 1200, "5月账单尾款"],
        ["5120000000000000002", None, "延后下单7月底", 2, 920],
    ]

    lines, _, _ = parse_sheet_rows(rows)
    assert [line.order_no for line in lines] == ["5120000000000000001"]
