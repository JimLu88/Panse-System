"""配件返厂/退货 财务闭环 (方案C, part_return_service)。"""
from __future__ import annotations

from decimal import Decimal

from app.models.inventory import PartInventory
from app.models.material import Material
from app.services import part_defect_service, part_return_service


def _seed_defective(db, *, code="AC-R01", physical=10, defective=5) -> PartInventory:
    db.add(Material(code=code, name=f"测试物料 {code}"))
    inv = PartInventory(
        warehouse="default", material_code=code,
        physical_qty=Decimal(str(physical)), locked_qty=Decimal("0"),
    )
    db.add(inv)
    db.commit()
    part_defect_service.mark_defective(db, material_code=code, qty=defective)
    db.commit()
    db.refresh(inv)
    return inv


def test_returned_creates_open_refund(db_session):
    inv = _seed_defective(db_session, defective=5)
    rec = part_return_service.record_resolution(
        db_session, material_code="AC-R01", qty=2, disposition="returned",
        amount=120, supplier="X配件厂", tracking_no="SF123")
    db_session.commit()
    db_session.refresh(inv)
    assert rec.amount_kind == "refund"
    assert rec.status == "open"               # 退货退款 → 待收
    assert float(rec.amount) == 120.0
    assert rec.supplier == "X配件厂"
    assert inv.defective_qty == Decimal("3")   # 库存也同步处置 (5-2)


def test_scrapped_creates_settled_loss(db_session):
    _seed_defective(db_session, defective=5)
    rec = part_return_service.record_resolution(
        db_session, material_code="AC-R01", qty=1, disposition="scrapped", amount=30)
    db_session.commit()
    assert rec.amount_kind == "scrap_loss"
    assert rec.status == "settled"            # 报废损失确认即结清


def test_repaired_creates_settled_fee(db_session):
    _seed_defective(db_session, defective=5)
    rec = part_return_service.record_resolution(
        db_session, material_code="AC-R01", qty=1, disposition="repaired", amount=15)
    db_session.commit()
    assert rec.amount_kind == "repair_fee"
    assert rec.status == "settled"


def test_settle_marks_received(db_session):
    _seed_defective(db_session, defective=5)
    rec = part_return_service.record_resolution(
        db_session, material_code="AC-R01", qty=2, disposition="returned", amount=120)
    db_session.commit()
    part_return_service.settle(db_session, rec.id, alipay_flow_no="TX-REFUND-1")
    db_session.commit()
    db_session.refresh(rec)
    assert rec.status == "settled"
    assert rec.alipay_flow_no == "TX-REFUND-1"


def test_summary_totals(db_session):
    _seed_defective(db_session, defective=10)
    part_return_service.record_resolution(
        db_session, material_code="AC-R01", qty=2, disposition="returned", amount=100)
    part_return_service.record_resolution(
        db_session, material_code="AC-R01", qty=1, disposition="scrapped", amount=30)
    part_return_service.record_resolution(
        db_session, material_code="AC-R01", qty=1, disposition="repaired", amount=15)
    db_session.commit()
    s = part_return_service.summary(db_session)
    assert s["pending_refund"] == 100.0
    assert s["scrap_loss_total"] == 30.0
    assert s["repair_fee_total"] == 15.0
    assert s["open_count"] == 1
    assert s["total_count"] == 3
