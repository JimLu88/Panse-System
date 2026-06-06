"""Task 10: 方案B 定制加价 — is_custom 订单理论成本 = 基础BOM成本 + custom_surcharge。"""
from __future__ import annotations

from decimal import Decimal

from app.models.bom import BomLine
from app.models.material import Material
from app.models.order import Order
from app.services import order_cost_service


def _seed_base(db):
    # 基础SKU S1 的 BOM: 1 个配件 AC-0001 单价10, 每件用2 -> 基础成本 20
    db.add(Material(code="AC-0001", name="测试板材", price=Decimal("10")))
    db.add(BomLine(product_code="P1", sku_code="S1", material_code="AC-0001", qty_per_product=2))
    db.commit()


def test_custom_cost_is_base_plus_surcharge(db_session):
    _seed_base(db_session)
    o = Order(platform="淘宝", order_no="O1", sku_code="S1",
              is_custom=True, custom_surcharge=Decimal("50"), qty=1)
    db_session.add(o)
    db_session.commit()
    bd = order_cost_service.recompute_and_save(db_session, o)
    assert o.theoretical_cost == Decimal("70")    # 基础20 + 加价50
    assert any(l.material_code == "定制加价" for l in bd.lines)


def test_non_custom_ignores_surcharge(db_session):
    _seed_base(db_session)
    o = Order(platform="淘宝", order_no="O2", sku_code="S1",
              is_custom=False, custom_surcharge=Decimal("50"), qty=1)
    db_session.add(o)
    db_session.commit()
    order_cost_service.recompute_and_save(db_session, o)
    assert o.theoretical_cost == Decimal("20")     # 非定制 -> 加价不生效


def test_custom_without_surcharge_is_base_only(db_session):
    _seed_base(db_session)
    o = Order(platform="淘宝", order_no="O3", sku_code="S1",
              is_custom=True, custom_surcharge=None, qty=1)
    db_session.add(o)
    db_session.commit()
    order_cost_service.recompute_and_save(db_session, o)
    assert o.theoretical_cost == Decimal("20")
