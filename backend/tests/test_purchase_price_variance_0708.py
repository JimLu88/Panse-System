"""配件采购 ↔ 物料库 价差预警 + 人工改价 (安全版, 2026-07-08)。"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.material import Material
from app.models.order import PartPurchase
from app.services import scanner_service as sc


def test_variance_flags_piece_outlier(db_session):
    db_session.add(Material(code="AC-9001", name="测试插座", category="电力轨道", unit="个", price=Decimal("75")))
    db_session.add(PartPurchase(purchase_no="P-OUT", material_code="AC-9001",
                                unit_price=Decimal("150"), purchase_date=date.today()))
    db_session.add(PartPurchase(purchase_no="P-OK", material_code="AC-9001",
                                unit_price=Decimal("78"), purchase_date=date.today()))   # +4% 不报
    db_session.flush()
    nos = {f.source_pk for f in sc.scan_purchase_price_outliers(db_session)}
    assert "P-OUT" in nos and "P-OK" not in nos


def test_variance_skips_area_priced(db_session):
    db_session.add(Material(code="AC-9002", name="测试岩板", category="岩板", unit="每平米", price=Decimal("220")))
    db_session.add(PartPurchase(purchase_no="P-SLAB", material_code="AC-9002",
                                unit_price=Decimal("1600"), purchase_date=date.today()))
    db_session.flush()
    nos = {f.source_pk for f in sc.scan_purchase_price_outliers(db_session)}
    assert "P-SLAB" not in nos   # 每平米料按块采购不比单价, 防误报


def test_apply_purchase_price_manual(db_session):
    db_session.add(Material(code="AC-9003", name="测试件", category="五金", unit="个", price=Decimal("50")))
    db_session.add(PartPurchase(purchase_no="P-APPLY", material_code="AC-9003", unit_price=Decimal("60")))
    db_session.flush()
    r = sc.apply_purchase_price(db_session, "P-APPLY")
    assert r["ok"] is True and r["old_price"] == 50.0 and r["new_price"] == 60.0
