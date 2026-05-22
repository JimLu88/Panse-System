"""Phase 1C: 库存锁定 / 释放 / 出货 / 退货流程."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.models.bom import BomLine
from app.models.inventory import PartInventory, ProductInventory
from app.models.inventory_lock import InventoryLockLedger
from app.models.material import Material
from app.models.order import FactoryOrder
from app.services import inventory_lock_service


def _setup_bom(db, product_code="P1"):
    """造一个 BOM: 1 件 P1 用 2 件 M001 + 3 件 M002."""
    db.add_all([
        Material(code="M001", name="木方", is_custom=False),
        Material(code="M002", name="五金", is_custom=False),
    ])
    db.flush()
    db.add_all([
        BomLine(product_code=product_code, material_code="M001", qty_per_product=Decimal("2")),
        BomLine(product_code=product_code, material_code="M002", qty_per_product=Decimal("3")),
    ])
    db.flush()


def _make_factory_order(db, product_code="P1", qty=1):
    fo = FactoryOrder(factory_order_no=f"FO{id(db) % 10000}", product_code=product_code,
                      qty=qty)
    db.add(fo); db.flush()
    return fo


def test_lock_basic(db_session):
    _setup_bom(db_session)
    db_session.add(PartInventory(warehouse="default", material_code="M001",
                                 physical_qty=10, locked_qty=0))
    db_session.add(PartInventory(warehouse="default", material_code="M002",
                                 physical_qty=10, locked_qty=0))
    db_session.flush()
    fo = _make_factory_order(db_session, qty=2)

    result = inventory_lock_service.lock_for_factory_order(db_session, fo.id)
    assert result.factory_order_id == fo.id
    assert len(result.locked_lines) == 2
    # M001: 2 * 2 = 4 锁定; M002: 3 * 2 = 6 锁定
    m1 = next(l for l in result.locked_lines if l["material_code"] == "M001")
    m2 = next(l for l in result.locked_lines if l["material_code"] == "M002")
    assert m1["qty_locked"] == 4
    assert m2["qty_locked"] == 6
    assert m1["available_after"] == 6  # 10 - 4
    assert m2["available_after"] == 4  # 10 - 6
    assert result.shortages == []
    assert result.alerts_created == []


def test_lock_shortage_creates_alert(db_session):
    _setup_bom(db_session)
    # M002 库存只有 2 件, 但下 1 件需要 3 件
    db_session.add(PartInventory(warehouse="default", material_code="M001",
                                 physical_qty=10, locked_qty=0))
    db_session.add(PartInventory(warehouse="default", material_code="M002",
                                 physical_qty=2, locked_qty=0))
    db_session.flush()
    fo = _make_factory_order(db_session, qty=1)

    result = inventory_lock_service.lock_for_factory_order(db_session, fo.id)
    # M002 缺 1
    assert len(result.shortages) == 1
    assert result.shortages[0]["material_code"] == "M002"
    assert result.shortages[0]["missing"] == 1
    # 应有 critical alert
    assert len(result.alerts_created) == 1
    from app.models.alert import Alert
    a = db_session.get(Alert, result.alerts_created[0])
    assert a.severity == "critical"
    assert a.kind == "low_stock_part"
    assert a.sticky is True


def test_release_frees_locked(db_session):
    _setup_bom(db_session)
    db_session.add(PartInventory(warehouse="default", material_code="M001",
                                 physical_qty=10))
    db_session.add(PartInventory(warehouse="default", material_code="M002",
                                 physical_qty=10))
    db_session.flush()
    fo = _make_factory_order(db_session, qty=2)
    inventory_lock_service.lock_for_factory_order(db_session, fo.id)
    db_session.flush()
    # 释放
    n = inventory_lock_service.release_factory_order_lock(
        db_session, fo.id, reason="客户取消",
    )
    assert n == 2  # 两个物料都释放
    # 锁定归零
    inv = db_session.execute(
        __import__("sqlalchemy").select(PartInventory).where(PartInventory.material_code == "M001")
    ).scalar_one()
    assert inv.locked_qty == 0


def test_release_resolves_shortage_alert(db_session):
    _setup_bom(db_session)
    db_session.add(PartInventory(warehouse="default", material_code="M001",
                                 physical_qty=10))
    db_session.add(PartInventory(warehouse="default", material_code="M002",
                                 physical_qty=1))  # 不够
    db_session.flush()
    fo = _make_factory_order(db_session, qty=1)
    r = inventory_lock_service.lock_for_factory_order(db_session, fo.id)
    assert len(r.alerts_created) == 1
    db_session.flush()

    inventory_lock_service.release_factory_order_lock(db_session, fo.id)
    db_session.flush()
    from app.models.alert import Alert
    a = db_session.get(Alert, r.alerts_created[0])
    assert a.resolved_at is not None
    assert a.resolved_by == "auto_after_release"


def test_consume_for_shipment(db_session):
    _setup_bom(db_session)
    db_session.add(PartInventory(warehouse="default", material_code="M001", physical_qty=10))
    db_session.add(PartInventory(warehouse="default", material_code="M002", physical_qty=10))
    db_session.flush()
    fo = _make_factory_order(db_session, qty=2)
    inventory_lock_service.lock_for_factory_order(db_session, fo.id)
    db_session.flush()

    n = inventory_lock_service.consume_for_shipment(db_session, fo.id)
    assert n == 2
    inv1 = db_session.execute(
        __import__("sqlalchemy").select(PartInventory).where(
            PartInventory.material_code == "M001"
        )
    ).scalar_one()
    assert inv1.physical_qty == 6  # 10 - 4
    assert inv1.locked_qty == 0


def test_inbound_part_resolves_shortage(db_session):
    """入库后应自动 resolve low_stock_part alert."""
    _setup_bom(db_session)
    db_session.add(PartInventory(warehouse="default", material_code="M001", physical_qty=0))
    db_session.add(PartInventory(warehouse="default", material_code="M002", physical_qty=10))
    db_session.flush()
    fo = _make_factory_order(db_session, qty=1)
    r = inventory_lock_service.lock_for_factory_order(db_session, fo.id)
    db_session.flush()
    assert len(r.alerts_created) == 1   # M001 缺货

    # 入 5 件 M001 (>= 2 锁定数)
    inventory_lock_service.inbound_part(
        db_session, material_code="M001", qty=5, actor="alice", remark="采购到货",
    )
    db_session.flush()
    from app.models.alert import Alert
    a = db_session.get(Alert, r.alerts_created[0])
    assert a.resolved_at is not None
    assert a.resolved_by == "auto_after_inbound"


def test_return_in_product_and_disassemble(db_session):
    _setup_bom(db_session)
    db_session.add(PartInventory(warehouse="default", material_code="M001", physical_qty=0))
    db_session.add(PartInventory(warehouse="default", material_code="M002", physical_qty=0))
    db_session.flush()

    # 退 1 件成品
    inv = inventory_lock_service.return_in_product(
        db_session, product_code="P1", sku_code=None, qty=1,
        actor="alice", remark="完好",
    )
    assert inv.physical_qty == 1

    # 拆 BOM → 成品 -1, 物料各加 2/3
    res = inventory_lock_service.disassemble_product_to_parts(
        db_session, product_code="P1", sku_code=None, qty=1,
    )
    assert res["product_remaining"] == 0
    m1 = db_session.execute(
        __import__("sqlalchemy").select(PartInventory).where(
            PartInventory.material_code == "M001"
        )
    ).scalar_one()
    assert m1.physical_qty == 2


def test_manual_adjust_writes_ledger(db_session):
    db_session.add(Material(code="M001", name="木方"))
    db_session.add(PartInventory(warehouse="default", material_code="M001", physical_qty=10))
    db_session.flush()
    inv = inventory_lock_service.manual_adjust(
        db_session, material_code="M001", new_physical=8,
        actor="alice", remark="实物只有 8 件",
    )
    assert inv.physical_qty == 8
    ledger = db_session.execute(
        __import__("sqlalchemy").select(InventoryLockLedger).where(
            InventoryLockLedger.kind == "count_adjust"
        )
    ).scalars().all()
    assert len(ledger) == 1
    assert ledger[0].qty == 2


def test_ledger_audit_trail(db_session):
    _setup_bom(db_session)
    db_session.add(PartInventory(warehouse="default", material_code="M001", physical_qty=10))
    db_session.add(PartInventory(warehouse="default", material_code="M002", physical_qty=10))
    db_session.flush()
    fo = _make_factory_order(db_session, qty=1)
    inventory_lock_service.lock_for_factory_order(db_session, fo.id)
    inventory_lock_service.release_factory_order_lock(db_session, fo.id)
    rows = inventory_lock_service.ledger_for_factory_order(db_session, fo.id)
    kinds = [r.kind for r in rows]
    assert kinds.count("lock") == 2
    assert kinds.count("release") == 2
