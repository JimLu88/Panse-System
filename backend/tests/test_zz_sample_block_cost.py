# -*- coding: utf-8 -*-
"""样块单成本口径 (2026-07-11 用户裁定): 每个样块订单固定¥5运费(小包快递), 商品成本=木作账单+打包。

病史: 1-4月逐单核对表合并把 actual_logistics 写成0, 经"定价表重构"分支把嵌入的5元运费冲没;
双块单 est_* 缩放不一致(eL=300/eP=170 大件脏值)时算出 1/0/负数 —— 全库样块成本曾有 0/1/6/8/13/14 七种。
(zz_ 前缀绕开既有 SQLite 连接污染排序坑。)"""
from datetime import date
from decimal import Decimal as D

from app.models.order import Order
from app.services import order_financials as ofin

_COEF = {"handling_rate": D("0.006"), "activity_rate": D("0.02"),
         "activity_since": date(2026, 5, 1), "activity_until": date(2026, 6, 30)}

_NAME = "畔色木作木块小样樱桃木黑胡桃木白蜡木榉木红白橡木样品样块"


def _sample(**kw):
    base = dict(order_no="YB1", product_name=_NAME, is_custom=False,
                paid_amount=D("22"), tax=D("0"), order_date=date(2026, 3, 1))
    base.update(kw)
    return Order(**base)


def test_billed_sample_wood_plus_packing():
    """有工厂账单: 商品成本 = 木作6 + 打包2 = 8 (merged aL=0 不再把运费冲成负调整)。"""
    o = _sample(actual_cost=D("6"), actual_packing=D("2"), actual_logistics=D("0"),
                est_logistics=D("5"), est_packing=D("2"), theoretical_cost=D("13"),
                wood_cost_est=D("6"))
    pb = ofin.physical_cost_breakdown(o)
    assert pb["cap_mode"] == "样块实账"
    assert pb["final"] == D("8")


def test_billed_sample_freight_defaults_5():
    """运费列: 有账单样块固定¥5/单 (actual_freight 空 → 默认5; 显式填了按实报)。"""
    o = _sample(actual_cost=D("6"), actual_packing=D("2"), wood_cost_est=D("6"))
    b = ofin.cost_breakdown(o, _COEF)
    assert b["freight"] == D("5")
    assert b["install_upstairs"] == D("0")
    o2 = _sample(actual_cost=D("6"), actual_packing=D("2"), actual_freight=D("5"),
                 wood_cost_est=D("6"))
    assert ofin.cost_breakdown(o2, _COEF)["freight"] == D("5")


def test_double_block_sample_no_est_junk():
    """双块单(木作12) + 大件est脏值(eL=300/eP=170): 成本=12+打包2=14, 不再被重构算成 1/0/负。"""
    o = _sample(paid_amount=D("44"), actual_cost=D("12"), actual_packing=D("2"),
                actual_logistics=D("0"), est_logistics=D("300"), est_packing=D("170"),
                theoretical_cost=D("19"), wood_cost_est=D("6"))
    pb = ofin.physical_cost_breakdown(o)
    assert pb["cap_mode"] == "样块实账"
    assert pb["final"] == D("14")
    assert ofin.cost_breakdown(o, _COEF)["freight"] == D("5")


def test_unbilled_sample_keeps_theoretical():
    """无工厂账单(近月账单未导): 照旧 theoretical 推演(13已含运费打包), 运费列0 防双算。"""
    o = _sample(actual_cost=None, theoretical_cost=D("13"), est_packing=D("2"))
    pb = ofin.physical_cost_breakdown(o)
    assert pb["cap_mode"] != "样块实账"
    assert pb["final"] == D("13")
    assert ofin.cost_breakdown(o, _COEF)["freight"] == D("0")


def test_non_sample_untouched():
    """真产品单不受样块分支影响 (纯账单非定制: 运费仍走 actual_freight)。"""
    assert not ofin.is_sample_order(Order(order_no="N1", product_name="畔色樱桃木靠墙一体实木岩板餐边柜"))
    o = Order(order_no="N2", product_name="畔色实木餐桌", is_custom=False, tax=D("0"),
              paid_amount=D("5000"), actual_cost=D("2000"), actual_freight=D("300"),
              order_date=date(2026, 3, 1))
    assert ofin.cost_breakdown(o, _COEF)["freight"] == D("300")