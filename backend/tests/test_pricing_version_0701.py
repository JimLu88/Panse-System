"""有效期定价 / 工厂调价历史 (用户 2026-07-01: 老单老价/新单新价, 历史利润不追溯改写)。"""
from datetime import date
from decimal import Decimal

import pytest

from app.models.pricing import PricingSku
from app.models.pricing_version import SENTINEL_START, PricingSkuVersion
from app.services import pricing_version_service as pv


def _sku(db, **kw):
    base = dict(product_code="P1", sku_code="S1", physical_cost=Decimal("500"),
                factory_cost=Decimal("450"), logistics_cost=Decimal("50"), install_cost=Decimal("0"),
                big_promo=Decimal("690"))
    base.update(kw)
    s = PricingSku(**base)
    db.add(s)
    db.commit()
    return s


def test_record_closes_period_with_old_values(db_session):
    s = _sku(db_session)                                  # 旧物理 500
    pv.record_dated_change(db_session, s, date(2026, 7, 1))
    s.physical_cost = Decimal("600")                       # 之后 caller 写新值
    db_session.commit()
    rows = db_session.query(PricingSkuVersion).all()
    assert len(rows) == 1
    assert rows[0].period_start == SENTINEL_START
    assert rows[0].period_end == date(2026, 7, 1)
    assert rows[0].physical_cost == Decimal("500")         # 封存的是改前(旧)值


def test_physical_at_old_before_new_after(db_session):
    s = _sku(db_session)
    pv.record_dated_change(db_session, s, date(2026, 7, 1))
    s.physical_cost = Decimal("600")
    db_session.commit()
    q = lambda d: pv.physical_at(db_session, sku_code="S1", product_code="P1", on_date=d)
    assert q(date(2026, 6, 30)) == Decimal("500")          # 分界日前 → 老价
    assert q(date(2026, 7, 1)) is None                     # 分界日当天起 → None(用 live 600)
    assert q(date(2026, 7, 15)) is None


def test_no_version_returns_none_uses_live(db_session):
    _sku(db_session)
    assert pv.physical_at(db_session, sku_code="S1", product_code="P1", on_date=date(2026, 6, 1)) is None


def test_none_date_returns_none(db_session):
    s = _sku(db_session)
    pv.record_dated_change(db_session, s, date(2026, 7, 1)); db_session.commit()
    assert pv.physical_at(db_session, sku_code="S1", product_code="P1", on_date=None) is None


def test_out_of_order_boundary_rejected(db_session):
    s = _sku(db_session)
    pv.record_dated_change(db_session, s, date(2026, 7, 1)); db_session.commit()
    with pytest.raises(ValueError):
        pv.record_dated_change(db_session, s, date(2026, 6, 1))   # 早于上个边界 → 拒绝


def test_two_changes_three_periods(db_session):
    s = _sku(db_session, physical_cost=Decimal("500"))
    pv.record_dated_change(db_session, s, date(2026, 5, 1)); s.physical_cost = Decimal("550"); db_session.commit()
    pv.record_dated_change(db_session, s, date(2026, 7, 1)); s.physical_cost = Decimal("600"); db_session.commit()
    q = lambda d: pv.physical_at(db_session, sku_code="S1", product_code="P1", on_date=d)
    assert q(date(2026, 4, 1)) == Decimal("500")           # [起点,5-01) 老老价
    assert q(date(2026, 6, 1)) == Decimal("550")           # [5-01,7-01) 中间价
    assert q(date(2026, 7, 2)) is None                     # >=7-01 → live 600


def test_product_code_fallback(db_session):
    """按 sku_code 无版本、但同 product_code 有版本 → 用 product_code 命中(与 _pricing_cost_for 回退对称)。"""
    s = _sku(db_session)
    pv.record_dated_change(db_session, s, date(2026, 7, 1)); s.physical_cost = Decimal("600"); db_session.commit()
    # 另一个 sku_code 但同 product_code
    assert pv.physical_at(db_session, sku_code="S_OTHER", product_code="P1", on_date=date(2026, 6, 1)) == Decimal("500")
