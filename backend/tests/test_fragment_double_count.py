"""片段封顶 + 物流双算护栏测试 (order_financials)。

用户拍板 2026-06-20:
- 片段封顶(选c): 实付<成本×50% → 实付×85%(定金/分期/差价单不背整份工厂成本)。
- 双算护栏: theoretical(含预测物流安装)的单不再单独加运费/安装; actual_cost(工厂价不含物流安装)的单才加。
全部 SimpleNamespace 合成对象, 无DB。
"""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.services.order_financials import cost_breakdown, physical_cost


def _coef():
    return {"handling_rate": Decimal("0.006"), "activity_rate": Decimal("0.02"),
            "activity_since": date(2026, 5, 1), "tax_rate": Decimal("0.02")}


def _o(actual=None, theo=None, paid="0", freight="0", install="0", upstairs="0"):
    return SimpleNamespace(
        actual_cost=Decimal(actual) if actual else None,
        theoretical_cost=Decimal(theo) if theo else None,
        paid_amount=Decimal(paid), actual_freight=Decimal(freight),
        install_fee=Decimal(install), upstairs_fee=Decimal(upstairs),
        tax=None, shop_received_amount=Decimal("0"), order_date=date(2026, 5, 1),
        # 真实 Order 必有的字段 (zero_cost_reason 直读): 补齐过时 mock, 防 AttributeError
        is_refill=False, sku=None, sku_code=None, product_name=None)


def test_fragment_cap_actual():
    # 定金单 ...228259: 工厂成本¥8200, 实付¥2335(<50%) → 封顶 2335×0.85
    assert physical_cost(_o(actual="8200", paid="2335")) == Decimal("1984.75")


def test_fragment_cap_theoretical():
    assert physical_cost(_o(theo="3300", paid="275")) == Decimal("233.75")


def test_no_cap_when_paid_enough():
    # 实付≥成本×50% → 不封顶, 全成本
    assert physical_cost(_o(actual="5240", paid="6333.66")) == Decimal("5240")


def test_normal_order_no_cap():
    assert physical_cost(_o(actual="2000", paid="3000")) == Decimal("2000")


def test_theoretical_freight_not_double_counted():
    # 用 theoretical(已含预测物流安装) + 填了运费/安装 → 不重复加(防双算)
    bd = cost_breakdown(_o(theo="2000", paid="3000", freight="200", install="100", upstairs="50"),
                        _coef(), aftersales=Decimal("0"))
    assert bd["freight"] == Decimal("0")
    assert bd["install_upstairs"] == Decimal("0")


def test_actual_cost_adds_real_freight():
    # 用 actual_cost(工厂价不含物流安装) → 单独加实际运费/安装
    bd = cost_breakdown(_o(actual="2000", paid="3000", freight="200", install="100", upstairs="50"),
                        _coef(), aftersales=Decimal("0"))
    assert bd["freight"] == Decimal("200")
    assert bd["install_upstairs"] == Decimal("150")
