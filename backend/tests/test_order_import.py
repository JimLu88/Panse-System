from datetime import date

from app.models.order import Order
from app.services import order_import


def test_import_basic_taobao_csv(db_session):
    csv_text = (
        "平台,订单编号,下单日期,客户姓名,产品编码,SKU,数量,实付金额\n"
        "淘宝,5112861625016010242,2026-04-28,张三,PPS26380040225,榉木床头柜-标准,1,1280.50\n"
        "淘宝,5112569342173038640,2026-04-28,李四,PPS26380040225,榉木床头柜-标准,2,2400\n"
    )
    report = order_import.import_orders_from_csv(db_session, csv_text)
    assert report.inserted == 2
    assert report.skipped_duplicate == 0
    rows = db_session.query(Order).order_by(Order.id).all()
    assert rows[0].order_no == "5112861625016010242"
    assert rows[0].customer_name == "张三"
    assert rows[0].order_date == date(2026, 4, 28)
    assert rows[0].qty == 1
    assert rows[1].qty == 2


def test_import_skips_duplicate_order_no(db_session):
    csv_text = (
        "平台,订单编号,数量\n"
        "淘宝,X1,1\n"
        "淘宝,X1,2\n"
    )
    # First pass
    r1 = order_import.import_orders_from_csv(db_session, csv_text)
    assert r1.inserted == 1
    assert r1.skipped_duplicate == 1


def test_import_skips_invalid_rows(db_session):
    csv_text = "订单编号,数量\n,5\nX1,1\n"
    report = order_import.import_orders_from_csv(db_session, csv_text)
    assert report.inserted == 1
    assert report.skipped_invalid == 1


def test_import_rejects_csv_without_order_no_column(db_session):
    csv_text = "平台,客户姓名\n淘宝,张三\n"
    report = order_import.import_orders_from_csv(db_session, csv_text)
    assert report.inserted == 0
    assert "订单编号" in report.errors[0]


def test_import_default_status_pending(db_session):
    csv_text = "订单编号\nX1\n"
    order_import.import_orders_from_csv(db_session, csv_text)
    o = db_session.query(Order).filter_by(order_no="X1").one()
    assert o.status == "pending_payment"


def test_import_parses_is_refill_yes(db_session):
    csv_text = "订单编号,是否补单\nX1,是\nX2,否\n"
    order_import.import_orders_from_csv(db_session, csv_text)
    assert db_session.query(Order).filter_by(order_no="X1").one().is_refill is True
    assert db_session.query(Order).filter_by(order_no="X2").one().is_refill is False
