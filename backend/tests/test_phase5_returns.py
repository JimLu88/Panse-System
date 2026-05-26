"""Phase 5: 退货闭环 (创建 → 签收 → 确认入库 / 损坏)."""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.alert import Alert
from app.models.bom import BomLine
from app.models.inventory import PartInventory, ProductInventory
from app.models.inventory_lock import InventoryLockLedger
from app.models.marketing import AfterSales
from app.models.material import Material
from app.models.order import Order
from app.services import return_service


def _make_order(db, order_no="O1", status="signed"):
    o = Order(platform="淘宝", order_no=order_no, qty=1, status=status,
              product_code="P1", paid_amount=Decimal("1000"))
    db.add(o); db.flush()
    return o


def test_create_return_without_tracking_creates_alert(db_session):
    _make_order(db_session)
    a = return_service.create_return(db_session, order_no="O1", reason="客户不喜欢")
    assert a.status == "pending_return"
    assert a.platform_order_no == "O1"
    al = db_session.execute(
        select(Alert).where(Alert.kind == "return_missing_tracking")
    ).scalar_one()
    assert al.dedupe_key == f"return_missing_tracking:{a.id}"
    assert al.sticky is True
    # 订单状态切到 aftersales
    o = db_session.execute(select(Order).where(Order.order_no == "O1")).scalar_one()
    assert o.status == "aftersales"


def test_create_return_with_tracking_no_alert(db_session):
    _make_order(db_session)
    return_service.create_return(db_session, order_no="O1",
                                 reason="x", tracking_no="SF123")
    rows = db_session.execute(
        select(Alert).where(Alert.kind == "return_missing_tracking")
    ).scalars().all()
    assert rows == []


def test_mark_received_creates_pending_alert(db_session):
    _make_order(db_session)
    a = return_service.create_return(db_session, order_no="O1", reason="x", tracking_no="t1")
    return_service.mark_received(db_session, a.id)
    db_session.refresh(a)
    assert a.status == "received_pending_inspection"
    pending = db_session.execute(
        select(Alert).where(Alert.kind == "return_pending_inspection")
    ).scalar_one()
    assert pending.sticky is True


def test_confirm_inbound_adds_to_product_inventory(db_session):
    _make_order(db_session)
    a = return_service.create_return(db_session, order_no="O1", reason="x", tracking_no="t1")
    return_service.mark_received(db_session, a.id)
    return_service.confirm_return_inbound(
        db_session, a.id, product_code="P1", qty=1, actor="alice",
    )
    db_session.refresh(a)
    assert a.second_inbound_confirmed == "是"
    assert a.status == "returned_in_stock"
    # 库存有了
    pinv = db_session.execute(
        select(ProductInventory).where(ProductInventory.product_code == "P1")
    ).scalar_one()
    assert pinv.physical_qty == 1
    # pending_inspection alert 解了
    pa = db_session.execute(
        select(Alert).where(Alert.kind == "return_pending_inspection")
    ).scalar_one()
    assert pa.resolved_at is not None


def test_confirm_inbound_is_idempotent(db_session):
    _make_order(db_session)
    a = return_service.create_return(db_session, order_no="O1", reason="x", tracking_no="t1")
    return_service.confirm_return_inbound(db_session, a.id, product_code="P1", qty=1)
    return_service.confirm_return_inbound(db_session, a.id, product_code="P1", qty=1)
    pinv = db_session.execute(
        select(ProductInventory).where(ProductInventory.product_code == "P1")
    ).scalar_one()
    assert pinv.physical_qty == 1   # 不重复入


def test_mark_damaged_does_not_inbound(db_session):
    _make_order(db_session)
    a = return_service.create_return(db_session, order_no="O1", reason="x", tracking_no="t1")
    return_service.mark_received(db_session, a.id)
    return_service.mark_return_damaged(
        db_session, a.id, reason="包装破损, 木板裂", actor="alice",
    )
    db_session.refresh(a)
    assert a.second_inbound_confirmed == "否"
    assert a.status == "damaged_not_inbound"
    # 没有 ProductInventory
    pinv = db_session.execute(
        select(ProductInventory).where(ProductInventory.product_code == "P1")
    ).scalar_one_or_none()
    assert pinv is None
    # 应该有损坏 alert
    da = db_session.execute(
        select(Alert).where(Alert.kind == "return_damaged_not_inbound")
    ).scalar_one()
    assert da is not None


def test_disassemble_after_return(db_session):
    """退货入库 P1 + 拆 BOM → 物料增加."""
    _make_order(db_session)
    # BOM: P1 = 2 件 M1
    db_session.add(Material(code="M1", name="木方"))
    db_session.add(BomLine(product_code="P1", material_code="M1",
                            qty_per_product=Decimal("2")))
    db_session.flush()
    a = return_service.create_return(db_session, order_no="O1", reason="x", tracking_no="t1")
    return_service.confirm_return_inbound(db_session, a.id, product_code="P1", qty=1)
    db_session.flush()

    from app.services import inventory_lock_service
    res = inventory_lock_service.disassemble_product_to_parts(
        db_session, product_code="P1", sku_code=None, qty=1,
    )
    assert res["product_remaining"] == 0
    inv = db_session.execute(
        select(PartInventory).where(PartInventory.material_code == "M1")
    ).scalar_one()
    assert inv.physical_qty == 2
    # ledger 有记录
    rows = db_session.execute(
        select(InventoryLockLedger).where(
            InventoryLockLedger.source_kind == "disassemble"
        )
    ).scalars().all()
    assert len(rows) == 1


def test_create_return_for_unknown_order_raises(db_session):
    with pytest.raises(ValueError):
        return_service.create_return(db_session, order_no="NOPE", reason="x")
