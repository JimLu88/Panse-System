from datetime import date
from decimal import Decimal

from app.models.finance import LogisticsBill
from app.models.order import Order, OrderDetail
from app.models.pricing import PricingSku
from app.services import logistics_measurement_service as service


def _sku(db, code="PPS2500000000101"):
    row = PricingSku(product_code="PPS25000000001", product_name="标准餐桌", sku="1.8米", sku_code=code)
    db.add(row); db.flush()
    return row


def _order(db, no, sku_code, qty=1, *, is_custom=False, remark=None):
    row = Order(
        platform="淘宝", order_no=no, status="signed", order_date=date(2026, 3, 1),
        product_code="PPS25000000001", product_name="标准餐桌", sku="1.8米",
        sku_code=sku_code, qty=qty, is_custom=is_custom, remark=remark,
    )
    db.add(row); db.flush()
    return row


def _bill(db, no, tracking, weight, volume):
    row = LogisticsBill(
        bill_date=date(2026, 3, 2), carrier="壹米滴答", tracking_no=tracking,
        order_no=no, match_method="manual", weight_kg=Decimal("99"),
        actual_weight_kg=Decimal(str(weight)), volume_m3=Decimal(str(volume)),
        freight_amount=Decimal("200"), row_type="line",
    )
    db.add(row); db.flush()
    return row


def test_refresh_uses_per_unit_median_and_never_billing_weight(db_session):
    sku = _sku(db_session)
    _order(db_session, "O1", sku.sku_code, qty=2)
    _order(db_session, "O2", sku.sku_code, qty=1)
    _bill(db_session, "O1", "T1", 20, 0.40)  # 每件 10kg / 0.20m³
    _bill(db_session, "O2", "T2", 12, 0.22)

    result = service.refresh_sku_shipping_measurements(db_session)

    assert result["updated_skus"] == 1
    assert sku.packaged_weight_kg == Decimal("11.000")
    assert sku.packaged_volume_m3 == Decimal("0.2100")
    assert sku.packaged_weight_kg != Decimal("99")
    assert sku.shipping_measure_sample_count == 2


def test_refresh_preserves_manual_packaged_values(db_session):
    sku = _sku(db_session)
    sku.packaged_weight_kg = Decimal("88")
    sku.packaged_weight_source = "manual"
    sku.packaged_volume_m3 = Decimal("0.8")
    sku.packaged_volume_source = "manual"
    _order(db_session, "O1", sku.sku_code)
    _bill(db_session, "O1", "T1", 20, 0.40)

    service.refresh_sku_shipping_measurements(db_session)

    assert sku.packaged_weight_kg == Decimal("88")
    assert sku.packaged_volume_m3 == Decimal("0.8")


def test_refresh_excludes_custom_and_multi_product_orders(db_session):
    sku = _sku(db_session)
    _order(db_session, "CUSTOM", sku.sku_code, remark="客户要求改尺寸")
    _bill(db_session, "CUSTOM", "TC", 20, 0.4)
    _order(db_session, "MULTI", sku.sku_code)
    db_session.add_all([
        OrderDetail(order_no="MULTI", sku_code=sku.sku_code, qty=1, source="import"),
        OrderDetail(order_no="MULTI", sku_code="PPS2500000000102", qty=1, source="import"),
    ])
    _bill(db_session, "MULTI", "TM", 40, 0.8)
    db_session.flush()

    result = service.refresh_sku_shipping_measurements(db_session)

    assert result["updated_skus"] == 0
    assert sku.packaged_weight_kg is None


def test_refresh_excludes_name_match_and_clears_previous_auto_value(db_session):
    sku = _sku(db_session)
    sku.packaged_weight_kg = Decimal("155")
    sku.packaged_weight_source = "bill"
    _order(db_session, "O1", sku.sku_code)
    bill = _bill(db_session, "O1", "T1", 85, 0.64)
    bill.match_method = "name_unique"

    result = service.refresh_sku_shipping_measurements(db_session)

    assert result["eligible_bills"] == 0
    assert sku.packaged_weight_kg is None
    assert sku.packaged_weight_source is None
