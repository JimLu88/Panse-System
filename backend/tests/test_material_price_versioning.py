# -*- coding: utf-8 -*-
"""物料价格按生效日版本化 (用户 2026-07-03): 成本按订单日取当时生效价, 改价前旧价、改价后新价。"""
from datetime import date
from decimal import Decimal

from app.models.material import Material
from app.services import material_price_service as mps


def test_material_price_at_fallback_and_seed(db_session):
    db = db_session
    db.add(Material(code="AC-9001", name="测试岩板A", price=Decimal("380")))
    db.flush()
    # 无历史 → 回退当前价
    assert mps.material_price_at(db, "AC-9001", date(2026, 3, 1)) == Decimal("380")
    # 种子基线(=当前价) → 任意订单日仍取到 380, 成本零变化
    r = mps.seed_baseline(db)
    assert r["seeded"] >= 1
    assert mps.material_price_at(db, "AC-9001", date(2026, 3, 1)) == Decimal("380")
    assert mps.material_price_at(db, "AC-9001", date(2025, 6, 1)) == Decimal("380")


def test_material_price_effective_by_order_date(db_session):
    db = db_session
    db.add(Material(code="AC-9002", name="测试岩板B", price=Decimal("380")))
    db.flush()
    mps.seed_baseline(db)                                         # 基线 380 @2025-01-01
    assert mps.record_change(db, "AC-9002", Decimal("500"), effective_from=date(2026, 7, 3))
    # 改价【前】的订单(3月) → 旧价 380; 改价【后】(7-3 起) → 500
    assert mps.material_price_at(db, "AC-9002", date(2026, 3, 1)) == Decimal("380")
    assert mps.material_price_at(db, "AC-9002", date(2026, 7, 3)) == Decimal("500")
    assert mps.material_price_at(db, "AC-9002", date(2026, 7, 10)) == Decimal("500")
    # 幂等: 同价再记不新增
    assert not mps.record_change(db, "AC-9002", Decimal("500"))


def test_seed_idempotent(db_session):
    db = db_session
    db.add(Material(code="AC-9003", name="测试岩板C", price=Decimal("220")))
    db.flush()
    a = mps.seed_baseline(db)["seeded"]
    b = mps.seed_baseline(db)["seeded"]      # 二次种子: 已有历史 → 不重复
    assert a >= 1 and b == 0
