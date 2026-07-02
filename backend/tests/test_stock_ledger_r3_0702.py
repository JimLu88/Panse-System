"""R3: 成品现货自动进出库 (product_stock_ledger_service)。

出库=发货扣现货(只扣有货的备货款, 幂等, 不为负); 入库=备货工厂单到货加现货(MTO单不加);
冲正=撤销发货/作废工厂单现货复原; 集成: transition→shipped 扣, shipped→cancelled 退回。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.inventory import ProductInventory, ProductStockMovement
from app.models.order import FactoryOrder, Order
from app.services import factory_order_service as fos
from app.services import order_service
from app.services import product_stock_ledger_service as psl


def _inv(db, code="P1", qty="5", sku="主款"):
    row = ProductInventory(warehouse="default", product_code=code, sku=sku,
                           physical_qty=Decimal(qty))
    db.add(row); db.flush()
    return row


def _order(db, code="P1", qty=2, sku="主款", status="paid", no="O1"):
    o = Order(platform="淘宝", order_no=no, order_date=date.today(),
              product_code=code, sku=sku, qty=qty, paid_amount=Decimal("100"),
              status=status, is_historical=False)
    db.add(o); db.flush()
    return o


# ---------------------------- 出库 ---------------------------- #

def test_record_shipment_deducts_held_stock(db_session):
    db = db_session
    _inv(db, "P1", "5")
    o = _order(db, "P1", 2)
    r = psl.record_shipment(db, o)
    assert r["deducted"] == 2.0
    inv = db.query(ProductInventory).filter_by(product_code="P1").one()
    assert inv.physical_qty == Decimal("3")
    assert inv.last_outbound_at == date.today()
    mv = db.query(ProductStockMovement).filter_by(reason="ship", entity_id=o.id).one()
    assert mv.qty == Decimal("-2")


def test_record_shipment_idempotent(db_session):
    db = db_session
    _inv(db, "P1", "5")
    o = _order(db, "P1", 2)
    psl.record_shipment(db, o)
    again = psl.record_shipment(db, o)          # 再发一次不重复扣
    assert again["deducted"] == 0.0
    inv = db.query(ProductInventory).filter_by(product_code="P1").one()
    assert inv.physical_qty == Decimal("3")     # 仍是 3, 没扣成 1


def test_record_shipment_no_stock_is_noop(db_session):
    """MTO 款: 没备货现货 → 不扣、不为负、不建流水。"""
    db = db_session
    _inv(db, "P1", "0")
    o = _order(db, "P1", 2)
    r = psl.record_shipment(db, o)
    assert r["deducted"] == 0.0
    assert db.query(ProductStockMovement).filter_by(reason="ship", entity_id=o.id).count() == 0
    inv = db.query(ProductInventory).filter_by(product_code="P1").one()
    assert inv.physical_qty == Decimal("0")     # 没被扣成 -2


def test_record_shipment_floors_at_zero(db_session):
    """现货 1 卖 3 → 扣到 0 为止, 不为负。"""
    db = db_session
    _inv(db, "P1", "1")
    o = _order(db, "P1", 3)
    r = psl.record_shipment(db, o)
    assert r["deducted"] == 1.0
    inv = db.query(ProductInventory).filter_by(product_code="P1").one()
    assert inv.physical_qty == Decimal("0")


# ---------------------------- 入库 ---------------------------- #

def test_restock_receipt_adds_stock(db_session):
    db = db_session
    _inv(db, "P1", "2")
    fo = FactoryOrder(factory_order_no="FO_R1", product_code="P1", qty=10,
                      order_date=date.today(), source_order_id=None)
    db.add(fo); db.flush()
    r = psl.record_restock_receipt(db, fo)
    assert r["added"] == 10.0
    inv = db.query(ProductInventory).filter_by(product_code="P1").one()
    assert inv.physical_qty == Decimal("12")
    assert inv.last_inbound_at == date.today()


def test_restock_receipt_mto_is_noop(db_session):
    """客户单(source_order_id 有值)到货不进可售现货。"""
    db = db_session
    _inv(db, "P1", "2")
    fo = FactoryOrder(factory_order_no="FO_MTO", product_code="P1", qty=5,
                      order_date=date.today(), source_order_id=999)
    db.add(fo); db.flush()
    r = psl.record_restock_receipt(db, fo)
    assert r["added"] == 0.0
    inv = db.query(ProductInventory).filter_by(product_code="P1").one()
    assert inv.physical_qty == Decimal("2")     # 没加


def test_restock_receipt_creates_row_if_missing(db_session):
    db = db_session
    fo = FactoryOrder(factory_order_no="FO_new", product_code="P9", qty=4,
                      order_date=date.today(), source_order_id=None)
    db.add(fo); db.flush()
    psl.record_restock_receipt(db, fo)
    inv = db.query(ProductInventory).filter_by(product_code="P9").one()
    assert inv.physical_qty == Decimal("4")


# ---------------------------- 冲正 ---------------------------- #

def test_reverse_ship_restores_stock(db_session):
    db = db_session
    _inv(db, "P1", "5")
    o = _order(db, "P1", 2)
    psl.record_shipment(db, o)                  # 5 → 3
    psl.reverse(db, "ship", "order", o.id)      # 退回 → 5
    inv = db.query(ProductInventory).filter_by(product_code="P1").one()
    assert inv.physical_qty == Decimal("5")
    # 再冲正一次不重复
    psl.reverse(db, "ship", "order", o.id)
    inv = db.query(ProductInventory).filter_by(product_code="P1").one()
    assert inv.physical_qty == Decimal("5")


# ---------------------------- 集成 ---------------------------- #

def test_transition_shipped_deducts_and_cancel_restores(db_session):
    db = db_session
    _inv(db, "P1", "5")
    o = _order(db, "P1", 2, status="paid")
    order_service.transition(db, o, "shipped", force=True, quiet=True)
    inv = db.query(ProductInventory).filter_by(product_code="P1").one()
    assert inv.physical_qty == Decimal("3")     # 发货扣 2
    order_service.transition(db, o, "cancelled", force=True, quiet=True)
    inv = db.query(ProductInventory).filter_by(product_code="P1").one()
    assert inv.physical_qty == Decimal("5")     # 取消退回


def test_void_factory_order_reverses_restock(db_session):
    db = db_session
    _inv(db, "P1", "1")
    fo = FactoryOrder(factory_order_no="FO_v", product_code="P1", qty=8,
                      order_date=date.today(), source_order_id=None)
    db.add(fo); db.flush()
    psl.record_restock_receipt(db, fo)          # 1 → 9
    assert db.query(ProductInventory).filter_by(product_code="P1").one().physical_qty == Decimal("9")
    fos.void_factory_order(db, fo.id, reason="test作废")
    assert db.query(ProductInventory).filter_by(product_code="P1").one().physical_qty == Decimal("1")


def test_check_negative_stock(db_session):
    db = db_session
    row = _inv(db, "P1", "0")
    row.physical_qty = Decimal("-3")
    db.flush()
    bad = psl.check_negative_stock(db)
    assert any(b["product_code"] == "P1" for b in bad)
