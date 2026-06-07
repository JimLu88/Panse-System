"""配件坏件 / 返厂维修闭环 (方案B, part_defect_service)。"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.inventory import PartInventory
from app.models.inventory_lock import InventoryLockLedger
from app.models.material import Material
from app.services import part_defect_service


def _seed(db, *, code="AC-T01", physical=10, locked=0) -> PartInventory:
    db.add(Material(code=code, name=f"测试物料 {code}"))
    inv = PartInventory(
        warehouse="default", material_code=code,
        physical_qty=Decimal(str(physical)), locked_qty=Decimal(str(locked)),
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)
    return inv


def test_mark_defective_moves_out_of_available(db_session):
    inv = _seed(db_session, physical=10)
    part_defect_service.mark_defective(
        db_session, material_code="AC-T01", qty=3, reason="到货裂了")
    db_session.commit()
    db_session.refresh(inv)
    assert inv.physical_qty == Decimal("7")
    assert inv.defective_qty == Decimal("3")
    assert inv.available_qty == Decimal("7")   # 可用随良品下降, 坏件不计入


def test_resolve_repaired_returns_to_good(db_session):
    inv = _seed(db_session, physical=10)
    part_defect_service.mark_defective(db_session, material_code="AC-T01", qty=4)
    part_defect_service.resolve_defective(
        db_session, material_code="AC-T01", qty=4, disposition="repaired")
    db_session.commit()
    db_session.refresh(inv)
    assert inv.defective_qty == Decimal("0")
    assert inv.physical_qty == Decimal("10")    # 修好全部回良品


def test_resolve_scrapped_writes_off(db_session):
    inv = _seed(db_session, physical=10)
    part_defect_service.mark_defective(db_session, material_code="AC-T01", qty=5)
    part_defect_service.resolve_defective(
        db_session, material_code="AC-T01", qty=5, disposition="scrapped")
    db_session.commit()
    db_session.refresh(inv)
    assert inv.defective_qty == Decimal("0")
    assert inv.physical_qty == Decimal("5")      # 报废不回良品


def test_mark_defective_guards_overdraw(db_session):
    _seed(db_session, physical=2)
    with pytest.raises(ValueError):
        part_defect_service.mark_defective(db_session, material_code="AC-T01", qty=5)


def test_resolve_guards_over_defective(db_session):
    _seed(db_session, physical=10)
    part_defect_service.mark_defective(db_session, material_code="AC-T01", qty=2)
    with pytest.raises(ValueError):
        part_defect_service.resolve_defective(
            db_session, material_code="AC-T01", qty=5, disposition="scrapped")


def test_resolve_unknown_disposition_raises(db_session):
    _seed(db_session, physical=10)
    part_defect_service.mark_defective(db_session, material_code="AC-T01", qty=2)
    with pytest.raises(ValueError):
        part_defect_service.resolve_defective(
            db_session, material_code="AC-T01", qty=1, disposition="bogus")


def test_ledger_entries_recorded(db_session):
    _seed(db_session, physical=10)
    part_defect_service.mark_defective(db_session, material_code="AC-T01", qty=3)
    part_defect_service.resolve_defective(
        db_session, material_code="AC-T01", qty=3, disposition="returned")
    db_session.commit()
    kinds = [r.kind for r in db_session.execute(
        select(InventoryLockLedger).where(InventoryLockLedger.material_code == "AC-T01")
    ).scalars()]
    assert "defect_out" in kinds
    assert "defect_return" in kinds
