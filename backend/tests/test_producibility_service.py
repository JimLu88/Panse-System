from decimal import Decimal

import pytest

from app.models.bom import BomLine
from app.models.inventory import PartInventory, ProductInventory
from app.models.material import Material
from app.services import producibility_service


def _setup_simple_bom(db):
    """1 件产品 = 1 件 AC-0001 + 4 件 AC-0009。"""
    db.add(Material(code="AC-0001", name="床铺板", unit="套", price=Decimal("300")))
    db.add(Material(code="AC-0009", name="金属腿", unit="个", price=Decimal("50")))
    db.add(BomLine(
        product_code="PPS001", sku="床-1.5米", sku_code="PPS00100000111",
        material_code="AC-0001", unit="套", qty_per_product=Decimal("1"),
    ))
    db.add(BomLine(
        product_code="PPS001", sku="床-1.5米", sku_code="PPS00100000111",
        material_code="AC-0009", unit="个", qty_per_product=Decimal("4"),
    ))
    db.flush()


def test_empty_state_zero_producible(db_session):
    _setup_simple_bom(db_session)
    r = producibility_service.compute(db_session, sku_code="PPS00100000111", target_qty=0)
    assert r.in_stock_qty == 0
    assert r.can_build_qty == 0
    assert r.total_available_qty == 0


def test_only_finished_inventory(db_session):
    _setup_simple_bom(db_session)
    db_session.add(ProductInventory(
        warehouse="W1", product_code="PPS001", sku="PPS00100000111", physical_qty=3, locked_qty=0,
    ))
    db_session.flush()
    r = producibility_service.compute(db_session, sku_code="PPS00100000111", target_qty=0)
    assert r.in_stock_qty == 3
    assert r.can_build_qty == 0
    assert r.total_available_qty == 3


def test_can_build_from_parts(db_session):
    _setup_simple_bom(db_session)
    # 10 套铺板, 20 个金属腿 → 铺板能造 10, 金属腿能造 5 → 瓶颈 = 金属腿 → 5
    db_session.add(PartInventory(warehouse="W1", material_code="AC-0001", physical_qty=10))
    db_session.add(PartInventory(warehouse="W1", material_code="AC-0009", physical_qty=20))
    db_session.flush()
    r = producibility_service.compute(db_session, sku_code="PPS00100000111", target_qty=0)
    assert r.can_build_qty == 5
    assert r.bottleneck is not None
    assert r.bottleneck.material_code == "AC-0009"


def test_finished_plus_parts(db_session):
    _setup_simple_bom(db_session)
    db_session.add(ProductInventory(warehouse="W1", product_code="PPS001", sku="PPS00100000111", physical_qty=2))
    db_session.add(PartInventory(warehouse="W1", material_code="AC-0001", physical_qty=3))
    db_session.add(PartInventory(warehouse="W1", material_code="AC-0009", physical_qty=12))  # 12/4=3
    db_session.flush()
    r = producibility_service.compute(db_session, sku_code="PPS00100000111", target_qty=0)
    assert r.in_stock_qty == 2
    assert r.can_build_qty == 3
    assert r.total_available_qty == 5


def test_locked_qty_excluded(db_session):
    _setup_simple_bom(db_session)
    # 10 件物理，6 件锁定，可用 = 4，金属腿够 → 铺板 4 / 金属腿 5 → 4
    db_session.add(PartInventory(warehouse="W1", material_code="AC-0001", physical_qty=10, locked_qty=6))
    db_session.add(PartInventory(warehouse="W1", material_code="AC-0009", physical_qty=20))
    db_session.flush()
    r = producibility_service.compute(db_session, sku_code="PPS00100000111", target_qty=0)
    assert r.can_build_qty == 4
    assert r.bottleneck.material_code == "AC-0001"


def test_missing_for_target_lists_shortages(db_session):
    _setup_simple_bom(db_session)
    db_session.add(PartInventory(warehouse="W1", material_code="AC-0001", physical_qty=2))
    db_session.add(PartInventory(warehouse="W1", material_code="AC-0009", physical_qty=5))
    db_session.flush()
    # 想造 10 件：铺板缺 8 套（10*1 - 2），金属腿缺 35（10*4 - 5）
    r = producibility_service.compute(db_session, sku_code="PPS00100000111", target_qty=10)
    missing = {m.material_code: m.shortage_for_target for m in r.missing_for_target}
    assert missing == {"AC-0001": Decimal("8"), "AC-0009": Decimal("35")}


def test_no_bom_no_build(db_session):
    # 没 BOM → 不能定量算可生产数
    db_session.add(ProductInventory(warehouse="W1", product_code="PPS999", sku="X", physical_qty=2))
    db_session.flush()
    r = producibility_service.compute(db_session, product_code="PPS999")
    assert r.in_stock_qty == 2
    assert r.can_build_qty == 0  # 无 BOM → 0
    assert r.bottleneck is None


def test_requires_some_identifier(db_session):
    with pytest.raises(ValueError):
        producibility_service.compute(db_session)


def test_negative_target_qty_rejected(db_session):
    _setup_simple_bom(db_session)
    with pytest.raises(ValueError):
        producibility_service.compute(db_session, sku_code="PPS00100000111", target_qty=-1)
