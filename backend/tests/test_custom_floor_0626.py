# -*- coding: utf-8 -*-
"""定制单成本 floor 到 实付×85% (用户 2026-06-26 选A)。

A: 定制单成本恒 ≥ 实付×85%(= max(木作占比推算, 实付×85%)), 净利≤~15%(工厂木作账单只含木作、不足信)。
   floor 只升不降(账单推算高于85%则保留真实推算); 排除小额追加/差价片段(实付<¥1500, 成本就该是那点)。
门槛从旧"占比推算毛利>30%才兜底"放宽到"所有定制单(实付≥1500)至少85%"。
"""
from decimal import Decimal
from app.models.order import Order
from app.services import order_financials as ofin


def test_custom_below_floor_raised_to_85():
    """图2形态: 定制单木作推算=2010/0.67≈3000 (占实付4000的75%, 毛利25%) < 实付×85%=3400 → floor 抬高到 3400。"""
    o = Order(order_no="F1", is_custom=True, paid_amount=Decimal("4000"), actual_cost=Decimal("2010"))
    assert ofin.physical_cost(o) == Decimal("3400.00")
    assert ofin.physical_cost_breakdown(o)["cap_mode"] == "定制兜底85"


def test_custom_above_floor_keeps_real():
    """账单高的定制单: 推算=2400/0.67≈3582 > 实付×85%=3400 → 保留真实推算, floor 不压低(只升不降)。"""
    o = Order(order_no="F2", is_custom=True, paid_amount=Decimal("4000"), actual_cost=Decimal("2400"))
    phys = ofin.physical_cost(o)
    assert abs(phys - Decimal("3582")) < Decimal("2")
    assert ofin.physical_cost_breakdown(o)["cap_mode"] == "none"


def test_custom_small_addon_not_floored():
    """小额追加单(实付200<¥1500, 仅追加插座等片段) → 不 floor, 成本保持木作推算≈149.25, 不被抬到 170(=200×0.85)。"""
    o = Order(order_no="F3", is_custom=True, paid_amount=Decimal("200"), actual_cost=Decimal("100"))
    phys = ofin.physical_cost(o)
    assert phys < Decimal("160")
    assert abs(phys - Decimal("149.25")) < Decimal("1")
    assert ofin.physical_cost_breakdown(o)["cap_mode"] == "none"


def test_custom_floor_at_threshold():
    """实付恰好¥1500的定制单(>=下限) → 走 floor: 木作推算300/0.67≈448 < 1500×0.85=1275 → 1275。"""
    o = Order(order_no="F4", is_custom=True, paid_amount=Decimal("1500"), actual_cost=Decimal("300"))
    assert ofin.physical_cost(o) == Decimal("1275.00")


def test_noncustom_unaffected_by_floor():
    """非定制单完全不受定制 floor 影响: 账单170 + 无定价参照 → 170。"""
    o = Order(order_no="F5", is_custom=False, sku_code="PPS777", paid_amount=Decimal("4000"),
              actual_cost=Decimal("170"), wood_cost_est=Decimal("0"), theoretical_cost=None)
    assert ofin.physical_cost(o) == Decimal("170")
