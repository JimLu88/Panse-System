"""配件采购价差异常: 自愈复核 + 更新物料库/0.85兜底 两种处理 (2026-07-08)。"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.exception import DataException
from app.models.material import Material
from app.models.order import PartPurchase
from app.services import exception_recheck_service as rc
from app.services import scanner_service as sc


def _seed(db):
    db.add(Material(code="AC-7001", name="测试插座", category="电力轨道", unit="个", price=Decimal("75")))
    db.add(PartPurchase(purchase_no="PV-1", material_code="AC-7001",
                        unit_price=Decimal("150"), purchase_date=date.today()))
    db.flush()


def _exc(db):
    e = DataException(source_table="part_purchases", source_pk="PV-1",
                      exception_type="purchase_price_variance", severity="warning",
                      description="采购价差", status="open", context={"purchase_no": "PV-1"})
    db.add(e)
    db.flush()
    return e


def test_recheck_holds_while_variance_remains(db_session):
    _seed(db_session)
    assert any(f.source_pk == "PV-1" for f in sc.scan_purchase_price_outliers(db_session))
    e = _exc(db_session)
    assert rc.recheck(db_session, e) is not None   # 仍偏离100% → 不销账


def test_update_material_selfheals(db_session):
    _seed(db_session)
    e = _exc(db_session)
    r = sc.apply_purchase_price(db_session, "PV-1")     # 更新物料库: 75 → 150
    assert r["ok"] and r["new_price"] == 150.0
    assert rc.recheck(db_session, e) is None            # 已一致 → 复核可销账
    closed = rc.bulk_close_resolved(db_session, types=["purchase_price_variance"])
    assert closed.get("purchase_price_variance", 0) >= 1
    db_session.refresh(e)
    assert e.status == "resolved"


def test_bulk_close_leaves_unresolved_open(db_session):
    _seed(db_session)
    e = _exc(db_session)
    rc.bulk_close_resolved(db_session, types=["purchase_price_variance"])
    db_session.refresh(e)
    assert e.status == "open"   # 没更新物料库 → 不自动关(0.85兜底走人工处理)
