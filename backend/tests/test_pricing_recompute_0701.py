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


# ── 成本加成价格链 (2026-07-01 对齐用户 Excel 定价法; 边柜榉木黑实数) ──────────────
def test_cost_plus_derives_excel_prices_from_base():
    """有基数 → 各档价 = ROUNDUP(物理÷(1−2.6%)÷基数,−1), 复刻用户 Excel 边柜价 (690/740/1460...)。"""
    s = _sku(factory_cost=Decimal("515"), factory_cost_override=True,
             logistics_cost=Decimal("50"), install_cost=Decimal("0"),
             base_list=Decimal("0.4"), base_small=Decimal("0.79"),
             base_mid=Decimal("0.85"), base_big=Decimal("0.85"))
    pc.recompute(s)
    assert s.physical_cost == Decimal("565.00")     # 515+50+0
    # 会计基准 = 565/0.974 = 580.082... → 各档 ROUNDUP 到 10
    assert s.list_price == Decimal("1460")          # ⌈580.08/0.4⌉10 = ⌈1450.2⌉10
    assert s.daily_price == Decimal("1095.00")      # 1460×0.75
    assert s.small_promo == Decimal("740")          # ⌈580.08/0.79⌉10 = ⌈734.3⌉10
    # 中促托底(2026-07-15 引入; ★任务#22 K统一1.03): base_mid给690=大促, 托底 ⌈690×1.03⌉10 = ⌈710.7⌉10 = 720
    assert s.mid_promo == Decimal("720")            # 中促自动联动大促(≥大促×K, K=1.03 全店统一)
    assert s.big_promo == Decimal("690")            # ⌈580.08/0.85⌉10 (Excel 边柜大促=690)
    assert s.gross_margin_rate > 0                   # 不再亏本 (原冻结价 503.25 时为 −14.9%)


def test_cost_up_raises_price_and_holds_margin():
    """核心诉求: 工厂成本一涨 → 大促价自动抬高 → 仍不亏 (毛利率保持正)。"""
    s = _sku(factory_cost=Decimal("515"), factory_cost_override=True,
             logistics_cost=Decimal("50"), install_cost=Decimal("0"), base_big=Decimal("0.85"))
    pc.recompute(s)
    big_before, rate_before = s.big_promo, s.gross_margin_rate
    assert big_before == Decimal("690")
    # 工厂成本涨 100 → 物理 665 → 大促应随之抬高、毛利率仍正
    s.factory_cost = Decimal("615")
    pc.recompute(s)
    assert s.physical_cost == Decimal("665.00")
    assert s.big_promo == Decimal("810")            # ⌈665/0.974/0.85⌉10 = ⌈803.2⌉10
    assert s.big_promo > big_before                  # 成本涨→价格涨 (联动!)
    assert s.gross_margin_rate > 0                   # 抬价后仍不亏
    assert abs(s.gross_margin_rate - rate_before) < Decimal("0.02")  # 毛利率大致守住


def test_no_base_preserves_input_big_promo():
    """无基数(base_* 全空) → 不走 cost-plus, 大促价保持原输入 (保护未对齐 SKU / 既有口径)。"""
    s = _sku(factory_cost=Decimal("515"), factory_cost_override=True,
             logistics_cost=Decimal("50"), install_cost=Decimal("0"), big_promo=Decimal("503.25"))
    pc.recompute(s)
    assert s.big_promo == Decimal("503.25")         # 未被推导覆盖
    assert s.list_price is None                      # 无 base_list → 不派生标价


def test_partial_base_only_big_leaves_others():
    """只填大促基数 → 只推导大促, 其余档 (标价等) 保持原值不动。"""
    s = _sku(factory_cost=Decimal("515"), factory_cost_override=True,
             logistics_cost=Decimal("50"), install_cost=Decimal("0"),
             list_price=Decimal("9999"), base_big=Decimal("0.85"))
    pc.recompute(s)
    assert s.big_promo == Decimal("690")            # 大促被推导
    assert s.list_price == Decimal("9999")          # 无 base_list → 标价保持原值
