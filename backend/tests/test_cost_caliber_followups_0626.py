# -*- coding: utf-8 -*-
"""成本口径后续修复回归 (2026-06-26, 用户拍板 4 项):
A. 非产品单(官方服务/专链/邮费/补拍/安装/送货)整单成本归零(复用 zero_cost_reason; 不含样块/差价)。
B. platform_deduction 退款钳制(退款最多扣到 实付−实收, 防 recv 非退款后净额时把退款多扣→平台费虚低)。
(C 多商品 fee 汇总 / D rollup 护栏 走线上前后比对验证, 依赖 OrderDetail/DB 数据。)
"""
from datetime import date
from decimal import Decimal

from app.models.order import Order
from app.services import order_financials as ofin


# ───────── A. 非产品单整单归零 ─────────

def test_nonproduct_link_zeroed():
    """产品名含「专链」=官方服务/非产品 → 整单成本 0(连工厂账单/打包都不计)。"""
    o = Order(order_no="NP1", is_custom=False, product_name="畔色木作 差价邮费补拍专链",
              actual_cost=Decimal("1280"), paid_amount=Decimal("2680"),
              theoretical_cost=Decimal("0"), est_packing=Decimal("170"))
    assert ofin.physical_cost(o) == Decimal("0")
    assert ofin.physical_cost_breakdown(o)["cap_mode"] == "非产品归零"


def test_nonproduct_install_zeroed():
    """产品名含「安装」官方服务 → 成本 0。"""
    o = Order(order_no="NP2", is_custom=False, product_name="商家安装服务上门",
              actual_cost=None, paid_amount=Decimal("300"),
              theoretical_cost=Decimal("0"), est_packing=Decimal("0"))
    assert ofin.physical_cost(o) == Decimal("0")


def test_real_product_not_zeroed():
    """正常产品(无官方服务关键词)不受影响: 仍走打包修复后的正常成本。"""
    o = Order(order_no="NP3", is_custom=False, sku_code="PPS001", product_name="畔色实木餐桌",
              actual_cost=None, paid_amount=Decimal("5000"),
              theoretical_cost=Decimal("2080"), est_packing=Decimal("180"))
    assert ofin.physical_cost(o) == Decimal("2080")


def test_sample_block_not_zeroed_by_rule_a():
    """样块(无官方服务关键词)不被本规则归零 —— 样块按实际¥13另算, 不归0。"""
    o = Order(order_no="NP4", is_custom=False, product_name="畔色木作木块小样樱桃木样块",
              actual_cost=Decimal("13"), paid_amount=Decimal("13"),
              theoretical_cost=Decimal("13"), est_packing=Decimal("0"))
    assert ofin.physical_cost(o) != Decimal("0")


# ───────── B. 平台费退款钳制 ─────────

_COEF = {"handling_rate": Decimal("0.006"), "activity_rate": Decimal("0.02"),
         "activity_since": date(2026, 5, 1), "activity_until": date(2026, 6, 30)}


def test_platform_refund_normal_unchanged():
    """正常(实收=退款后净额, 退款≤实付−实收): 平台费 = 实付−实收−退款, 不变。"""
    o = Order(order_no="PL1", paid_amount=Decimal("1000"),
              shop_received_amount=Decimal("970"), refund_amount=Decimal("20"),
              order_date=date(2026, 4, 1))
    assert ofin.platform_deduction(o, _COEF) == Decimal("10")   # 30 − 20


def test_platform_refund_overclamp():
    """退款(50) > 实付−实收(30): 钳到30 → diff=0(平台费0), 不再转负落率算法虚高/虚低。"""
    o = Order(order_no="PL2", paid_amount=Decimal("1000"),
              shop_received_amount=Decimal("970"), refund_amount=Decimal("50"),
              order_date=date(2026, 4, 1))
    assert ofin.platform_deduction(o, _COEF) == Decimal("0")


def test_platform_no_shop_received_uses_rate():
    """无店铺实收 → 率算法(实付×手续费), 与钳制无关。"""
    o = Order(order_no="PL3", paid_amount=Decimal("1000"), shop_received_amount=None,
              refund_amount=Decimal("0"), order_date=date(2026, 4, 1))
    assert ofin.platform_deduction(o, _COEF) == Decimal("6.00")   # 1000 × 0.006
