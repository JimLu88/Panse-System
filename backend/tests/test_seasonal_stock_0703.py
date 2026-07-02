"""重点备货月 / 季节系数: 备货用「目标月(今天+提前期)」系数, 4月为5月峰前瞻放大、7月比6月峰回落。"""
from __future__ import annotations

from datetime import date

from app.services import product_inventory_service as pis

FACTORS = [1.0, 0.3, 1.0, 1.0, 6.0, 6.0, 0.7, 1.0, 1.0, 5.0, 7.0, 5.0]


def _cfg(enable=True, factors=None):
    return {"enable_seasonal": enable, "seasonal_factors": factors or FACTORS, "window_days": 60}


def test_april_boosts_for_may_peak():
    daily, tm, mult = pis._seasonal_effective_daily(_cfg(), 1.0, 30, today=date(2026, 4, 10))
    assert tm == 5              # 4月10 + 30天 = 5月10 → 瞄准5月
    assert mult > 5            # 最近(2-3月淡) → 5月峰: 大幅前瞻放大
    assert daily > 5           # base 1.0 × mult → 前瞻日均放大到 5 倍以上


def test_july_dampens_after_june_peak():
    daily, tm, mult = pis._seasonal_effective_daily(_cfg(), 1.0, 30, today=date(2026, 7, 10))
    assert tm == 8              # 7月10 + 30 = 8月9 → 瞄准8月(常态)
    assert mult < 0.5          # 最近含5-6月峰 → 压回常态


def test_seasonal_off_returns_base():
    daily, tm, mult = pis._seasonal_effective_daily(_cfg(enable=False), 2.0, 30, today=date(2026, 7, 10))
    assert daily == 2.0 and mult == 1.0 and tm is None


def test_all_ones_no_change():
    daily, tm, mult = pis._seasonal_effective_daily(_cfg(factors=[1.0] * 12), 3.0, 30, today=date(2026, 4, 10))
    assert abs(daily - 3.0) < 1e-9 and abs(mult - 1.0) < 1e-9


def test_config_roundtrip_seasonal(db_session):
    pis.save_forecast_config(db_session, {"seasonal_factors": FACTORS, "enable_seasonal": False,
                                          "seasonal_auto": True})
    cfg = pis.get_forecast_config(db_session)
    assert cfg["seasonal_factors"] == FACTORS
    assert cfg["enable_seasonal"] is False
    assert cfg["seasonal_auto"] is True


def test_default_seasonal_on_with_seed(db_session):
    cfg = pis.get_forecast_config(db_session)
    assert cfg["enable_seasonal"] is True          # 默认开
    assert cfg["seasonal_factors"] == FACTORS       # 默认种子(用户经验值)
    assert cfg["seasonal_auto"] is False            # 自动进化默认关


def test_recompute_keeps_seed_when_thin(db_session):
    r = pis.recompute_seasonal_factors(db_session)
    assert len(r["factors"]) == 12
    assert r["updated_months"] == []                # 无数据 → 全保留种子
    assert r["factors"] == r["current"]
