"""实际账单覆盖预估 — physical_cost 替换逻辑测试 (用户 2026-06-21)。

成本 = 原成本 − 预估 + 实际(只换精确配到逐单的分量); 未配/无预估保持原样; 防双减。
"""
from decimal import Decimal

from app.models.order import Order
from app.services.order_financials import physical_cost


def _o(**kw):
    kw.setdefault("platform", "淘宝")
    kw.setdefault("order_no", "X")
    kw.setdefault("qty", 1)
    kw.setdefault("paid_amount", Decimal("9999"))  # 高实付, 不触发片段封顶
    return Order(**kw)


def test_packing_actual_replaces_estimate(db_session):
    o = _o(theoretical_cost=Decimal("1000"), est_packing=Decimal("100"), actual_packing=Decimal("150"))
    assert physical_cost(o) == Decimal("1050")   # 1000 − 100 + 150


def test_logistics_actual_replaces_estimate(db_session):
    o = _o(theoretical_cost=Decimal("1000"), est_logistics=Decimal("200"), actual_logistics=Decimal("180"))
    assert physical_cost(o) == Decimal("980")     # 1000 − 200 + 180


def test_both_swapped(db_session):
    o = _o(theoretical_cost=Decimal("1000"),
           est_packing=Decimal("100"), actual_packing=Decimal("150"),
           est_logistics=Decimal("200"), actual_logistics=Decimal("180"))
    assert physical_cost(o) == Decimal("1030")    # 1000 −100+150 −200+180


def test_unmatched_keeps_estimate(db_session):
    """没有 actual(没配到)→ 不替换, 保持预估总成本。"""
    o = _o(theoretical_cost=Decimal("1000"), est_packing=Decimal("100"))
    assert physical_cost(o) == Decimal("1000")


def test_no_estimate_no_swap(db_session):
    """有 actual 但无 est(定价表缺)→ 不替换(防乱减), 保持原样。"""
    o = _o(theoretical_cost=Decimal("1000"), actual_packing=Decimal("150"))
    assert physical_cost(o) == Decimal("1000")


def test_factory_actual_cost_with_wood_est_swaps(db_session):
    """工厂账单单(actual_cost=木作) + wood_est 补非木作 → 预估在 cost 里, 可替换。"""
    o = _o(actual_cost=Decimal("700"), wood_cost_est=Decimal("700"),
           theoretical_cost=Decimal("1000"),   # 非木作=300(含预估打包100)
           est_packing=Decimal("100"), actual_packing=Decimal("150"))
    # cost = 700 + (1000-700) = 1000; 换打包 −100+150 = 1050
    assert physical_cost(o) == Decimal("1050")


def test_factory_actual_cost_no_wood_est_no_swap(db_session):
    """工厂账单单但无 wood_est(没补非木作)→ 预估不在 cost 里, 不替换(防双减)。"""
    o = _o(actual_cost=Decimal("700"),
           est_packing=Decimal("100"), actual_packing=Decimal("150"))
    assert physical_cost(o) == Decimal("700")   # 保持 actual_cost, 不动


def test_fragment_cap_after_swap(db_session):
    """片段封顶: 替换后再判定 实付<成本50% → 实付×85%。"""
    o = _o(theoretical_cost=Decimal("1000"), paid_amount=Decimal("100"),
           est_logistics=Decimal("200"), actual_logistics=Decimal("180"))
    # swap → 980; 实付100 < 980×50% → 100×0.85 = 85
    assert physical_cost(o) == Decimal("85.00")
