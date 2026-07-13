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


def test_no_bill_fragment_uses_actuals_only():
    """无工厂账单+有实配件 (用户 2026-07-14): 只算实际, 不带整件估值 —
    差价单实付200挂着 wood_est 5100/est_parts 1871 曾被算成 ¥8302, 应为实配件75。"""
    o = Order(order_no="AP6", is_custom=False, paid_amount=Decimal("200"),
              actual_parts=Decimal("75"), wood_cost_est=Decimal("5100"),
              est_parts=Decimal("1871.05"), est_packing=Decimal("450"),
              est_logistics=Decimal("700"), est_install=Decimal("181"))
    pb = ofin.physical_cost_breakdown(o)
    assert pb["cap_mode"] == "片段实配件"
    assert pb["final"] == Decimal("75")            # 只有实配件, est_* 一概不入
    assert pb["factory_wood"] == Decimal("0")
    assert pb["logistics_component"] == Decimal("0")


def test_no_bill_fragment_includes_actual_extras():
    """无账单片段带实际打包/物流 → 一并计入(仍不带估值)。"""
    o = Order(order_no="AP7", is_custom=False, paid_amount=Decimal("408"),
              actual_parts=Decimal("346.80"), actual_packing=Decimal("20"),
              actual_logistics=Decimal("30"), wood_cost_est=Decimal("1610"),
              est_parts=Decimal("800"))
    pb = ofin.physical_cost_breakdown(o)
    assert pb["cap_mode"] == "片段实配件"
    assert pb["final"] == Decimal("396.80")        # 346.80 + 30物流 + 20打包
    assert pb["logistics_component"] == Decimal("30")


def test_no_bill_fullprice_stays_itemized():
    """无账单但实付≥整件木作估(真实单等账单, 如…3049 实付12235>木作估5200) → 不进片段门,
    仍整件逐项(木作估5200 + 配件预估 + …), 不被低估。"""
    o = Order(order_no="AP8", is_custom=False, paid_amount=Decimal("12235.71"),
              actual_parts=Decimal("1024.74"), wood_cost_est=Decimal("5200"),
              est_parts=Decimal("1871.05"))
    pb = ofin.physical_cost_breakdown(o)
    assert pb["cap_mode"] == "实配件分项"
    assert pb["factory_wood"] == Decimal("5200")
    assert pb["final"] == Decimal("5200") + Decimal("1871.05")   # 木作估+配件预估(无物流/打包)


def test_actual_parts_prefers_pricing_estimate():
    """配件项预估优先 (用户 2026-07-13): est_parts>0 时归集值(352.03 类)不再计入 —
    木作2300+物流500+安装75+打包190+配件预估240.90 = 3305.90 (而非用 352.03 的 3417.03)。"""
    o = Order(order_no="AP5", is_custom=False, paid_amount=Decimal("3531"),
              actual_cost=Decimal("2300"), actual_logistics=Decimal("500"),
              actual_install=Decimal("75"), actual_packing=Decimal("190"),
              actual_parts=Decimal("352.03"), est_parts=Decimal("240.90"))
    pb = ofin.physical_cost_breakdown(o)
    assert pb["cap_mode"] == "实配件分项"
    assert pb["final"] == Decimal("3305.90")
    # 配件分量 = estimate_part − 物流 − 安装 = 预估配件
    assert pb["estimate_part"] - pb["logistics_component"] - pb["install_component"] == Decimal("240.90")
