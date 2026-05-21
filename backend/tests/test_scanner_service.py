from datetime import date
from decimal import Decimal

import pytest

from app.models.exception import DataException
from app.models.finance import AlipayFlow
from app.models.inventory import PartInventory
from app.models.material import Material
from app.models.order import Order
from app.models.product import Product
from app.services import scanner_service as svc


# -------- dangling_order_product --------

def test_dangling_product_code_detected(db_session):
    db_session.add(Product(code="PPS001", name="X"))
    db_session.add(Order(platform="淘宝", order_no="O1", product_code="PPS999", qty=1))
    db_session.flush()
    r = svc.run_scanner(db_session, "dangling_order_product", dry_run=True)
    assert len(r.findings) == 1
    assert r.findings[0].source_pk == "O1"
    assert r.findings[0].context["product_code"] == "PPS999"


def test_dangling_product_writes_exception(db_session):
    db_session.add(Order(platform="淘宝", order_no="O1", product_code="MISSING", qty=1))
    db_session.flush()
    r = svc.run_scanner(db_session, "dangling_order_product")
    assert r.written == 1
    excs = db_session.query(DataException).all()
    assert excs[0].exception_type == "dangling_product_code"


def test_scanner_dedups_existing_open_exception(db_session):
    db_session.add(Order(platform="淘宝", order_no="O1", product_code="MISSING", qty=1))
    db_session.flush()
    r1 = svc.run_scanner(db_session, "dangling_order_product")
    r2 = svc.run_scanner(db_session, "dangling_order_product")
    assert r1.written == 1
    assert r2.written == 0
    assert r2.skipped_duplicate == 1
    assert db_session.query(DataException).count() == 1


def test_valid_product_no_findings(db_session):
    db_session.add(Product(code="PPS001", name="X"))
    db_session.add(Order(platform="淘宝", order_no="O1", product_code="PPS001", qty=1))
    db_session.flush()
    r = svc.run_scanner(db_session, "dangling_order_product", dry_run=True)
    assert r.findings == []


# -------- negative_inventory --------

def test_negative_inventory_detected(db_session):
    db_session.add(Material(code="AC-0001", name="X"))
    db_session.add(PartInventory(warehouse="W1", material_code="AC-0001", physical_qty=2, locked_qty=5))
    db_session.flush()
    r = svc.run_scanner(db_session, "negative_inventory", dry_run=True)
    assert len(r.findings) == 1
    assert r.findings[0].severity == "error"


def test_zero_inventory_no_findings(db_session):
    db_session.add(Material(code="AC-0001", name="X"))
    db_session.add(PartInventory(warehouse="W1", material_code="AC-0001", physical_qty=0, locked_qty=0))
    db_session.flush()
    r = svc.run_scanner(db_session, "negative_inventory", dry_run=True)
    assert r.findings == []


# -------- numeric_range --------

def test_non_positive_price_detected(db_session):
    db_session.add(Material(code="AC-0001", name="A", price=Decimal("0")))
    db_session.add(Material(code="AC-0002", name="B", price=Decimal("-10")))
    db_session.add(Material(code="AC-0003", name="C", price=Decimal("100")))  # OK
    db_session.flush()
    r = svc.run_scanner(db_session, "numeric_range", dry_run=True)
    codes = {f.source_pk for f in r.findings}
    assert codes == {"AC-0001", "AC-0002"}


# -------- date_logic --------

def test_ship_before_order_detected(db_session):
    db_session.add(Order(
        platform="淘宝", order_no="O1",
        order_date=date(2026, 5, 10), ship_date=date(2026, 5, 5),
        qty=1,
    ))
    db_session.flush()
    r = svc.run_scanner(db_session, "date_logic", dry_run=True)
    assert len(r.findings) == 1
    assert r.findings[0].exception_type == "ship_before_order"


def test_ship_after_order_ok(db_session):
    db_session.add(Order(
        platform="淘宝", order_no="O1",
        order_date=date(2026, 5, 1), ship_date=date(2026, 5, 5),
        qty=1,
    ))
    db_session.flush()
    r = svc.run_scanner(db_session, "date_logic", dry_run=True)
    assert r.findings == []


# -------- missing_custom_price --------

def test_custom_missing_price_detected(db_session):
    db_session.add(Material(code="AC-1001", name="定制A", is_custom=True, price=None))
    db_session.add(Material(code="AC-1002", name="定制B", is_custom=True, price=Decimal("50")))  # OK
    db_session.add(Material(code="AC-0001", name="标准C", is_custom=False, price=None))  # 非定制不查
    db_session.flush()
    r = svc.run_scanner(db_session, "missing_custom_price", dry_run=True)
    assert {f.source_pk for f in r.findings} == {"AC-1001"}


# -------- duplicate_alipay_cross_account --------

def test_duplicate_alipay_flow_detected(db_session):
    db_session.add(AlipayFlow(account="A", transaction_no="T1", amount=Decimal("100")))
    db_session.add(AlipayFlow(account="B", transaction_no="T1", amount=Decimal("100")))
    db_session.add(AlipayFlow(account="A", transaction_no="T2", amount=Decimal("50")))
    db_session.flush()
    r = svc.run_scanner(db_session, "duplicate_alipay_cross_account", dry_run=True)
    assert len(r.findings) == 1
    assert r.findings[0].source_pk == "T1"
    assert set(r.findings[0].context["accounts"]) == {"A", "B"}


# -------- meta --------

def test_run_all_executes_every_scanner(db_session):
    results = svc.run_all(db_session, dry_run=True)
    assert set(results.keys()) == set(svc.SCANNERS.keys())


def test_unknown_scanner_raises(db_session):
    with pytest.raises(ValueError):
        svc.run_scanner(db_session, "nope")
