# -*- coding: utf-8 -*-
"""逐单真实配件 actual_parts → 逐项真实计价 (用户 2026-06-26, migration 0094)。

填了真实配件 → physical_cost = 木作 + 物流 + 安装 + 打包 + 真实配件(各分项 actual 优先否则 est),
跳过占比估算与实付×85% floor。默认空时行为完全不变(回归)。
"""
from decimal import Decimal
from app.models.order import Order
from app.services import order_financials as ofin


def test_actual_parts_itemized():
    """填真实配件 → 逐项真实之和: 木作2010+物流300+安装100+打包200+配件500 = 3110, cap=实配件分项。"""
    o = Order(order_no="AP1", is_custom=True, paid_amount=Decimal("4000"),
              actual_cost=Decimal("2010"), actual_logistics=Decimal("300"),
              actual_install=Decimal("100"), actual_packing=Decimal("200"),
              actual_parts=Decimal("500"))
    assert ofin.physical_cost(o) == Decimal("3110")
    assert ofin.physical_cost_breakdown(o)["cap_mode"] == "实配件分项"


def test_actual_parts_overrides_floor():
    """有真实配件 → 不被实付×85% floor 抬高: 真实和3110 < 4000×85%=3400, 仍按真实 3110。"""
    o = Order(order_no="AP2", is_custom=True, paid_amount=Decimal("4000"),
              actual_cost=Decimal("2010"), actual_logistics=Decimal("300"),
              actual_install=Decimal("100"), actual_packing=Decimal("200"),
              actual_parts=Decimal("500"))
    assert ofin.physical_cost(o) == Decimal("3110")


def test_actual_parts_uses_est_when_actual_missing():
    """配件真实填了, 物流/安装/打包无 actual → 用 est 兜底: 2010+350+80+170+500 = 3110。"""
    o = Order(order_no="AP3", is_custom=True, paid_amount=Decimal("4000"),
              actual_cost=Decimal("2010"), est_logistics=Decimal("350"),
              est_install=Decimal("80"), est_packing=Decimal("170"),
              actual_parts=Decimal("500"))
    assert ofin.physical_cost(o) == Decimal("3110")


def test_no_actual_parts_unchanged():
    """没填真实配件 → 行为完全不变(回归): 仍走占比推算 + 实付×85% floor = 3400。"""
    o = Order(order_no="AP4", is_custom=True, paid_amount=Decimal("4000"), actual_cost=Decimal("2010"))
    assert ofin.physical_cost(o) == Decimal("3400.00")
    assert ofin.physical_cost_breakdown(o)["cap_mode"] == "定制兜底85"
