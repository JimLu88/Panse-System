"""定价表 recompute 全链联动 (用户 2026-07-01: "改工厂成本不重算/任意改动都要联动")。

成本链: 工厂成本=木作+包装+外配件(除非override) → 物理=工厂+物流+安装 → 会计=物理+平台费+税 → 利润。
"""
from decimal import Decimal

from app.models.pricing import PricingSku
from app.services import pricing_calc_service as pc


def _sku(**kw):
    base = dict(product_code="P1", sku_code="S1")
    base.update(kw)
    return PricingSku(**base)


def test_cost_chain_cascades_from_components():
    """改组件 → 工厂成本自动=三者和 → 物理=工厂+物流+安装 → 会计/利润全联动。"""
    s = _sku(wood_cost=Decimal("200"), packaging_cost=Decimal("50"), external_parts_cost=Decimal("165"),
             logistics_cost=Decimal("50"), install_cost=Decimal("0"), big_promo=Decimal("503.25"))
    pc.recompute(s)
    assert s.factory_cost == Decimal("415.00")      # 200+50+165
    assert s.physical_cost == Decimal("465.00")     # 415+50+0
    assert s.platform_fee_rate == Decimal("3.02")   # 503.25×0.006=3.0195→3.02
    assert s.tax == Decimal("10.07")                # 503.25×0.02=10.065→10.07
    assert s.accounting_cost == Decimal("478.09")   # 465+3.02+10.07
    assert s.big_promo_margin == Decimal("25.16")   # 503.25−478.09


def test_factory_override_kept_and_drives_physical():
    """手动覆盖工厂成本(override=True): 不被组件覆盖, 物理=手改工厂+物流+安装。"""
    s = _sku(wood_cost=Decimal("200"), packaging_cost=Decimal("50"), external_parts_cost=Decimal("165"),
             logistics_cost=Decimal("50"), install_cost=Decimal("0"),
             factory_cost=Decimal("515"), factory_cost_override=True, big_promo=Decimal("1000"))
    pc.recompute(s)
    assert s.factory_cost == Decimal("515")         # 保留手改, 不回 415
    assert s.physical_cost == Decimal("565.00")     # 515+50+0 (这正是之前没联动的 bug 点)


def test_change_logistics_cascades_to_physical():
    """改物流 → 物理联动(工厂自动派生不变, 物理=工厂+新物流+安装)。"""
    s = _sku(wood_cost=Decimal("200"), packaging_cost=Decimal("50"), external_parts_cost=Decimal("165"),
             logistics_cost=Decimal("80"), install_cost=Decimal("0"), big_promo=Decimal("1000"))
    pc.recompute(s)
    assert s.factory_cost == Decimal("415.00")
    assert s.physical_cost == Decimal("495.00")     # 415+80+0


def test_no_override_default_false():
    """默认 override=False: 工厂成本始终随组件; 即使先前有手填值也会被组件派生覆盖。"""
    s = _sku(wood_cost=Decimal("100"), packaging_cost=Decimal("0"), external_parts_cost=Decimal("0"),
             logistics_cost=Decimal("0"), install_cost=Decimal("0"),
             factory_cost=Decimal("999"), big_promo=Decimal("500"))
    pc.recompute(s)
    assert s.factory_cost == Decimal("100.00")      # 非override → 回到组件和, 不留 999
    assert s.physical_cost == Decimal("100.00")
