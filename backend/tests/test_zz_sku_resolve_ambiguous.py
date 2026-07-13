# -*- coding: utf-8 -*-
"""SKU 描述反查多义护栏 (2026-07-13)。

案发: 「尺寸微定制」等通用描述在多产品下各有一行定价, 缺 sku_code 的订单按描述反查
命中多行 → scalar_one_or_none 抛 MultipleResultsFound, 夜间「订单自动维护(22:50)」与
「理论成本兜底反推(06:50)」整轮炸停。修: 唯一命中才用, 多义返回 None 走缺成本兜底。
"""
from decimal import Decimal

from app.models.order import Order
from app.models.pricing import PricingSku
from app.services import order_cost_service as ocs


def _ps(code, sku):
    return PricingSku(sku_code=code, product_code=code[:14], sku=sku,
                      list_price=Decimal("100"))


def test_ambiguous_sku_text_returns_none(db_session):
    """同名描述多行 → 反查返回 None(不崩、不瞎挑)。"""
    db_session.add_all([_ps("PPS2421004051399", "定制专拍"),
                        _ps("PPS2421006050199", "定制专拍")])
    db_session.commit()
    o = Order(order_no="AMB1", sku="定制专拍", qty=1)
    assert ocs._resolve_sku_code(db_session, o) is None


def test_unique_sku_text_resolves(db_session):
    """唯一命中 → 正常解析出 sku_code。"""
    db_session.add(_ps("PPS2521010041012", "岩板餐桌1.6唯一款"))
    db_session.commit()
    o = Order(order_no="AMB2", sku="岩板餐桌1.6唯一款", qty=1)
    assert ocs._resolve_sku_code(db_session, o) == "PPS2521010041012"


def test_recompute_all_survives_ambiguous(db_session):
    """整轮反推不再被多义单炸停 (夜间任务恢复)。"""
    db_session.add_all([_ps("PPS2421004051399", "定制专拍"),
                        _ps("PPS2421006050199", "定制专拍")])
    db_session.add(Order(order_no="AMB3", platform="淘宝", sku="定制专拍", qty=1,
                         paid_amount=Decimal("100"), status="signed"))
    db_session.commit()
    r = ocs.recompute_all(db_session, only_missing=True)
    assert r is not None   # 不抛 MultipleResultsFound 即通过
