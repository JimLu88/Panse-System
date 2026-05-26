"""Phase 7: 历史水位线 + 定制 BOM 复用 + 盘点二次确认."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.alert import Alert
from app.models.bom import BomLine
from app.models.custom_variant import CustomVariant
from app.models.inventory import PartInventory
from app.models.inventory_lock import InventoryLockLedger
from app.models.material import Material
from app.models.order import Order
from app.services import baseline_service, count_adjust_service, customization_service


# ============================ 水位线 ============================ #


def test_set_baseline_marks_historical(db_session):
    today = date.today()
    db_session.add_all([
        Order(platform="淘宝", order_no="OLD1",
              order_date=today - timedelta(days=200),
              product_code="P1", qty=1, status="signed"),
        Order(platform="淘宝", order_no="NEW1",
              order_date=today, product_code="P1", qty=1, status="signed"),
    ])
    db_session.flush()
    baseline = today - timedelta(days=100)
    r = baseline_service.set_baseline_date(db_session, baseline)
    assert r["marked"] == 1
    old = db_session.execute(select(Order).where(Order.order_no == "OLD1")).scalar_one()
    new = db_session.execute(select(Order).where(Order.order_no == "NEW1")).scalar_one()
    assert old.is_historical is True
    assert new.is_historical is False


def test_get_baseline(db_session):
    assert baseline_service.get_baseline_date(db_session) is None
    baseline_service.set_baseline_date(db_session, date(2026, 1, 1))
    assert baseline_service.get_baseline_date(db_session) == date(2026, 1, 1)


def test_clear_baseline_does_not_unmark(db_session):
    today = date.today()
    db_session.add(Order(platform="x", order_no="O1",
                          order_date=today - timedelta(days=100),
                          status="signed"))
    db_session.flush()
    baseline_service.set_baseline_date(db_session, today - timedelta(days=30))
    baseline_service.clear_baseline(db_session)
    o = db_session.execute(select(Order).where(Order.order_no == "O1")).scalar_one()
    # 标记不回滚 (设计选择)
    assert o.is_historical is True


# ============================ 定制 BOM 复用 ============================ #


def _setup_base_sku(db, sku_code="S1"):
    db.add(Material(code="M001", name="木方", is_custom=False))
    db.add(BomLine(product_code="P1", sku_code=sku_code,
                    material_code="M001", qty_per_product=Decimal("2"),
                    size_type="组合"))
    db.flush()


def test_customization_reuses_existing_size_material(db_session):
    """已有同尺寸的定制物料时复用, 不再新建."""
    _setup_base_sku(db_session)
    # 造一个已存在的定制物料: 木方 @ 长=1.8m
    # 用规范化签名 (sorted) 入库, 匹配时也按 sorted 算
    sig = customization_service._size_signature({"长": "1.8", "宽": "0.5"})
    db_session.add(Material(
        code="M001-1800",
        name="木方 (1.8m)",
        is_custom=True,
        remark=f"size_sig={sig}",
    ))
    db_session.flush()
    diff = customization_service._build_diff(
        db_session, "S1", {"长": "1.8", "宽": "0.5"},
    )
    assert len(diff) == 1
    # 应该复用 M001-1800
    assert diff[0].material_code == "M001-1800"
    assert "复用" in (diff[0].note or "")


def test_customization_creates_new_when_no_match(db_session):
    """没有同尺寸时 fallback 用 base material."""
    _setup_base_sku(db_session)
    diff = customization_service._build_diff(
        db_session, "S1", {"长": "2.0"},
    )
    assert diff[0].material_code == "M001"   # 没复用, 用原 code
    assert diff[0].requires_new_material is True


# ============================ 盘点二次确认 ============================ #


def test_propose_count_adjust_does_not_change_qty(db_session):
    db_session.add(Material(code="M1", name="x"))
    db_session.add(PartInventory(warehouse="default", material_code="M1",
                                  physical_qty=Decimal("10")))
    db_session.flush()
    entry = count_adjust_service.propose(
        db_session, material_code="M1", new_physical=8,
        actor="alice", remark="盘点少了 2",
    )
    inv = db_session.execute(
        select(PartInventory).where(PartInventory.material_code == "M1")
    ).scalar_one()
    assert inv.physical_qty == Decimal("10")    # 没改
    assert entry.kind == "count_pending"
    # 应该有审批 alert
    a = db_session.execute(
        select(Alert).where(Alert.kind == "count_adjust_pending")
    ).scalar_one()
    assert a.sticky is True


def test_approve_count_adjust_applies(db_session):
    db_session.add(Material(code="M1", name="x"))
    db_session.add(PartInventory(warehouse="default", material_code="M1",
                                  physical_qty=Decimal("10")))
    db_session.flush()
    entry = count_adjust_service.propose(
        db_session, material_code="M1", new_physical=8,
        actor="alice", remark="盘点",
    )
    count_adjust_service.approve(db_session, entry.id, approver="boss")
    inv = db_session.execute(
        select(PartInventory).where(PartInventory.material_code == "M1")
    ).scalar_one()
    assert inv.physical_qty == Decimal("8")    # 改了
    # alert 自动解决
    a = db_session.execute(
        select(Alert).where(Alert.kind == "count_adjust_pending")
    ).scalar_one()
    assert a.resolved_at is not None


def test_reject_count_adjust_no_change(db_session):
    db_session.add(Material(code="M1", name="x"))
    db_session.add(PartInventory(warehouse="default", material_code="M1",
                                  physical_qty=Decimal("10")))
    db_session.flush()
    entry = count_adjust_service.propose(
        db_session, material_code="M1", new_physical=8,
        actor="alice", remark="盘点",
    )
    count_adjust_service.reject(db_session, entry.id, approver="boss",
                                 reason="数据有误")
    inv = db_session.execute(
        select(PartInventory).where(PartInventory.material_code == "M1")
    ).scalar_one()
    assert inv.physical_qty == Decimal("10")
    db_session.refresh(entry)
    assert entry.kind == "count_rejected"


def test_list_pending(db_session):
    db_session.add(Material(code="M1", name="x"))
    db_session.add(PartInventory(warehouse="default", material_code="M1",
                                  physical_qty=Decimal("10")))
    db_session.flush()
    count_adjust_service.propose(db_session, material_code="M1", new_physical=8,
                                  actor="a", remark="r1")
    count_adjust_service.propose(db_session, material_code="M1", new_physical=5,
                                  actor="b", remark="r2")
    pending = count_adjust_service.list_pending(db_session)
    assert len(pending) == 2
