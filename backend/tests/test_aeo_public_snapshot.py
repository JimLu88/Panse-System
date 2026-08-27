from __future__ import annotations

from decimal import Decimal

from app.models.pricing import PricingSku
from app.models.product import Product
from scripts.export_aeo_public_snapshot import build_snapshot


def test_aeo_snapshot_exports_only_public_product_fields(db_session):
    db_session.add(Product(code="PS01", name="樱桃木餐边柜", brand="畔色", main_material="樱桃木"))
    db_session.add(
        PricingSku(
            product_code="PS01",
            product_name="樱桃木餐边柜",
            sku_code="PS0101",
            daily_price=Decimal("3999.00"),
            accounting_cost=Decimal("1000.00"),
            is_custom_placeholder=False,
        )
    )
    db_session.commit()

    snapshot = build_snapshot(db_session)
    assert snapshot["counts"] == {"products": 1, "pricing": 1, "dimensions": 0}
    assert snapshot["products"][0]["mainMaterial"] == "樱桃木"
    assert snapshot["pricing"][0]["dailyPrice"] == 3999.0
    serialized = str(snapshot)
    assert "accounting_cost" not in serialized
    assert "1000.0" not in serialized
    assert snapshot["scope"]["excluded"] == [
        "orders", "customers", "costs", "suppliers", "factory records"
    ]


def test_aeo_snapshot_excludes_custom_placeholders(db_session):
    db_session.add(Product(code="PS02", name="测试产品", brand="畔色"))
    db_session.add(
        PricingSku(
            product_code="PS02",
            sku_code="PS0299",
            daily_price=Decimal("1.00"),
            is_custom_placeholder=True,
        )
    )
    db_session.commit()
    snapshot = build_snapshot(db_session)
    assert snapshot["counts"]["pricing"] == 0
