from datetime import date
from decimal import Decimal

from app.api.products import SkuShippingMeasurementPatch, update_sku_shipping_measurements
from app.models.pricing import PricingSku
from app.models.product import Product


def _sku(db_session):
    product = Product(code="PPS-MEASURE-1", name="物流参数测试产品")
    sku = PricingSku(
        product_code=product.code,
        product_name=product.name,
        sku="1.8米",
        sku_code="PPS-MEASURE-101",
        packaged_weight_kg=Decimal("89"),
        packaged_volume_m3=Decimal("0.57"),
        packaged_weight_source="bill",
        packaged_volume_source="bill",
        shipping_measure_source_tracking_no="700800000001",
        shipping_measure_source_date=date(2026, 3, 2),
        shipping_measure_sample_count=3,
    )
    db_session.add_all([product, sku])
    db_session.commit()
    return sku


def test_editing_bare_values_does_not_turn_unchanged_bill_values_manual(db_session):
    sku = _sku(db_session)

    update_sku_shipping_measurements(
        sku.product_code,
        sku.id,
        SkuShippingMeasurementPatch(
            product_weight_kg=70,
            packaged_weight_kg=89,
            product_volume_m3=0.4,
            packaged_volume_m3=0.57,
        ),
        db_session,
        None,
    )

    assert sku.product_weight_kg == Decimal("70")
    assert sku.packaged_weight_source == "bill"
    assert sku.packaged_volume_source == "bill"
    assert sku.shipping_measure_source_tracking_no == "700800000001"


def test_changed_packaged_values_become_manual_and_clear_bill_provenance(db_session):
    sku = _sku(db_session)

    update_sku_shipping_measurements(
        sku.product_code,
        sku.id,
        SkuShippingMeasurementPatch(packaged_weight_kg=91, packaged_volume_m3=0.6),
        db_session,
        None,
    )

    assert sku.packaged_weight_source == "manual"
    assert sku.packaged_volume_source == "manual"
    assert sku.shipping_measure_source_tracking_no is None
    assert sku.shipping_measure_source_date is None
    assert sku.shipping_measure_sample_count is None
