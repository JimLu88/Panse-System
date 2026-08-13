from datetime import date
from decimal import Decimal

from app.models.finance import LogisticsBill
from app.models.order import Order, OrderDetail
from app.services import logistics_analytics_service as analytics


def _order(db, no: str, product: str, *, sku: str = "标准", code: str = "P1"):
    db.add(Order(
        platform="淘宝", order_no=no, qty=1, status="signed", order_date=date(2026, 5, 1),
        customer_name="测试客户", customer_address="浙江省杭州市西湖区",
        product_name=product, product_code=code, sku=sku, sku_code=f"{code}-01",
    ))
    db.flush()


def _detail(
    db,
    no: str,
    sub: str,
    product: str,
    *,
    sku: str = "标准",
    code: str = "P1",
    qty: int = 1,
    amount: str | None = None,
):
    db.add(OrderDetail(
        sync_key=f"line:{sub}", sub_order_no=sub, order_no=no, source="import",
        product_name=product, product_code=code, sku_name=sku, sku_code=f"{code}-01",
        qty=qty, amount=Decimal(amount) if amount is not None else None,
        factory_delivery_required=True,
    ))
    db.flush()


def _bill(db, no: str | None, *, day: int, fee: str, destination: str = "浙江省-杭州市-西湖区", weight: str = "100", volume: str = "0.8"):
    row = LogisticsBill(
        bill_date=date(2026, 5, day), carrier="德邦", tracking_no=f"DB{day:02d}{no or 'X'}",
        order_no=no, weight_kg=Decimal(weight), actual_weight_kg=Decimal(weight) - 5,
        volume_m3=Decimal(volume), package_count=2, freight_amount=Decimal(fee),
        destination=destination, row_type="line", match_method="manual" if no else "none",
    )
    db.add(row)
    db.flush()
    return row


def test_product_context_uses_order_details_and_marks_multi_product(db_session):
    _order(db_session, "O1", "主表旧名称")
    _detail(db_session, "O1", "S1", "榉木餐桌", sku="1.8米", code="TABLE")
    _detail(db_session, "O1", "S2", "安装服务", code="SERVICE")
    _order(db_session, "O2", "组合订单")
    _detail(db_session, "O2", "S3", "榉木餐桌", sku="1.8米", code="TABLE")
    _detail(db_session, "O2", "S4", "榉木床头柜", sku="标准", code="CAB")

    value = analytics.product_context_by_order(db_session, ["O1", "O2"])
    assert value["O1"]["product_name"] == "榉木餐桌"
    assert value["O1"]["product_analytics_eligible"] is True
    assert value["O1"]["product_display"] == "榉木餐桌 · 1.8米"
    assert value["O2"]["is_multi_product"] is True
    assert value["O2"]["product_analytics_eligible"] is False
    assert "榉木餐桌" in value["O2"]["product_display"]
    assert "榉木床头柜" in value["O2"]["product_display"]


def test_parse_region_handles_province_city_and_municipality():
    assert analytics.parse_region("浙江省-杭州市-西湖区") == ("浙江省", "杭州市")
    assert analytics.parse_region("上海-上海市-浦东新区") == ("上海市", "上海市")
    assert analytics.parse_region(None) == ("未知", "未知")


def test_analytics_excludes_multi_product_from_product_averages(db_session):
    _order(db_session, "O1", "榉木餐桌", sku="1.8米", code="TABLE")
    _detail(db_session, "O1", "S1", "榉木餐桌", sku="1.8米", code="TABLE")
    _order(db_session, "O2", "组合订单")
    _detail(db_session, "O2", "S2", "榉木餐桌", sku="1.8米", code="TABLE")
    _detail(db_session, "O2", "S3", "榉木床头柜", sku="标准", code="CAB")
    _bill(db_session, "O1", day=1, fee="180")
    _bill(db_session, "O2", day=2, fee="300")

    value = analytics.build_analytics(db_session)
    assert value["overview"]["shipment_count"] == 2
    assert value["overview"]["single_product_count"] == 1
    assert value["overview"]["multi_product_count"] == 1
    assert len(value["products"]) == 1
    assert value["products"][0]["product_name"] == "榉木餐桌"
    assert value["products"][0]["avg_freight"] == 180
    assert value["regions"][0]["city"] == "杭州市"
    assert value["overview"]["volume_coverage"] == 100


def test_anomaly_requires_same_product_sku_province_and_three_samples(db_session):
    for index, fee in enumerate(("100", "110", "260"), start=1):
        no = f"O{index}"
        _order(db_session, no, "榉木餐桌", sku="1.8米", code="TABLE")
        _detail(db_session, no, f"S{index}", "榉木餐桌", sku="1.8米", code="TABLE")
        _bill(db_session, no, day=index, fee=fee)
    value = analytics.build_analytics(db_session)
    assert len(value["anomalies"]) == 1
    assert value["anomalies"][0]["freight_amount"] == 260
    assert value["anomalies"][0]["sample_count"] == 3


def test_filters_and_product_month_change(db_session):
    _order(db_session, "O1", "榉木餐桌", sku="1.8米", code="TABLE")
    _detail(db_session, "O1", "S1", "榉木餐桌", sku="1.8米", code="TABLE")
    _bill(db_session, "O1", day=1, fee="180")
    _order(db_session, "O2", "榉木床头柜", sku="标准", code="CAB")
    _detail(db_session, "O2", "S2", "榉木床头柜", sku="标准", code="CAB")
    _bill(db_session, "O2", day=2, fee="90", destination="北京市-朝阳区")

    value = analytics.build_analytics(db_session, product="餐桌", province="浙江省")
    assert value["overview"]["shipment_count"] == 1
    assert value["products"][0]["product_name"] == "榉木餐桌"
    assert value["options"]["products"] == ["榉木床头柜", "榉木餐桌"]


def test_unmatched_bill_remains_visible_but_not_in_product_stats(db_session):
    _bill(db_session, None, day=1, fee="88")
    value = analytics.build_analytics(db_session)
    assert value["overview"]["shipment_count"] == 1
    assert value["overview"]["unmatched_product_count"] == 1
    assert value["products"] == []


def test_same_product_multiple_quantity_is_not_mixed_into_single_item_average(db_session):
    _order(db_session, "O1", "榉木餐桌", sku="1.8米", code="TABLE")
    _detail(db_session, "O1", "S1", "榉木餐桌", sku="1.8米", code="TABLE", qty=2)
    _bill(db_session, "O1", day=1, fee="260")
    value = analytics.build_analytics(db_session)
    context = analytics.product_context_by_order(db_session, ["O1"])["O1"]
    assert context["is_multi_quantity"] is True
    assert context["product_qty"] == 2
    assert context["product_analytics_eligible"] is False
    assert value["overview"]["multi_quantity_count"] == 1
    assert value["products"] == []


def test_refunded_import_detail_does_not_fall_back_to_order_product(db_session):
    _order(db_session, "O1", "主订单旧商品", sku="标准", code="OLD")
    _detail(
        db_session, "O1", "S1", "已退款榉木餐桌",
        sku="1.8米", code="TABLE", amount="100",
    )
    line = db_session.query(OrderDetail).filter(OrderDetail.sub_order_no == "S1").one()
    line.refund_amount = Decimal("100")
    line.refund_status = "退款成功"
    db_session.flush()

    context = analytics.product_context_by_order(db_session, ["O1"])["O1"]
    assert context["product_display"] is None
    assert context["product_analytics_eligible"] is False
    assert context["product_analytics_reason"] == "product_unresolved"


def test_partially_refunded_signed_detail_keeps_physical_product(db_session):
    _order(db_session, "O1", "主订单旧商品", sku="标准", code="OLD")
    _detail(
        db_session, "O1", "S1", "榉木餐桌",
        sku="1.8米", code="TABLE", amount="2716.05",
    )
    line = db_session.query(OrderDetail).filter(OrderDetail.sub_order_no == "S1").one()
    line.line_status = "signed"
    line.refund_amount = Decimal("8.16")
    line.refund_status = "退款成功"
    db_session.flush()

    context = analytics.product_context_by_order(db_session, ["O1"])["O1"]
    assert context["product_name"] == "榉木餐桌"
    assert context["product_analytics_eligible"] is True


def test_refund_status_without_comparable_amount_stays_excluded(db_session):
    _order(db_session, "O1", "主订单旧商品", sku="标准", code="OLD")
    _detail(db_session, "O1", "S1", "榉木餐桌", sku="1.8米", code="TABLE")
    line = db_session.query(OrderDetail).filter(OrderDetail.sub_order_no == "S1").one()
    line.refund_status = "退款成功"
    db_session.flush()

    context = analytics.product_context_by_order(db_session, ["O1"])["O1"]
    assert context["product_display"] is None
    assert context["product_analytics_reason"] == "product_unresolved"


def test_partial_refund_amount_without_refund_status_stays_excluded(db_session):
    _order(db_session, "O1", "主订单旧商品", sku="标准", code="OLD")
    _detail(
        db_session, "O1", "S1", "榉木餐桌",
        sku="1.8米", code="TABLE", amount="2716.05",
    )
    line = db_session.query(OrderDetail).filter(OrderDetail.sub_order_no == "S1").one()
    line.line_status = "signed"
    line.refund_amount = Decimal("500")
    line.refund_status = None
    db_session.flush()

    context = analytics.product_context_by_order(db_session, ["O1"])["O1"]
    assert context["product_display"] is None
    assert context["product_analytics_reason"] == "product_unresolved"


def test_returned_detail_stays_excluded_even_when_refund_is_partial(db_session):
    _order(db_session, "O1", "主订单旧商品", sku="标准", code="OLD")
    _detail(
        db_session, "O1", "S1", "榉木餐桌",
        sku="1.8米", code="TABLE", amount="2716.05",
    )
    line = db_session.query(OrderDetail).filter(OrderDetail.sub_order_no == "S1").one()
    line.line_status = "signed"
    line.refund_amount = Decimal("500")
    line.refund_status = "退货退款成功"
    db_session.flush()

    context = analytics.product_context_by_order(db_session, ["O1"])["O1"]
    assert context["product_display"] is None
    assert context["product_analytics_reason"] == "product_unresolved"


def test_order_fallback_filters_service_link_but_keeps_custom_product(db_session):
    _order(db_session, "O1", "差价邮费补拍专链", sku="补差", code="TOPUP")
    _order(db_session, "O2", "榉木餐桌定制尺寸专拍链接", sku="1.9米", code="TABLE")

    context = analytics.product_context_by_order(db_session, ["O1", "O2"])
    assert context["O1"]["product_display"] is None
    assert context["O1"]["product_analytics_reason"] == "product_unresolved"
    assert context["O2"]["product_name"] == "榉木餐桌定制尺寸专拍链接"
    assert context["O2"]["product_analytics_eligible"] is True
