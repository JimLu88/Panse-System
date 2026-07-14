# -*- coding: utf-8 -*-
"""cost_exceeds_paid 判据改引擎同源 (用户选A 2026-07-14)。

字段层假警: 全量重算把片段单 theoretical 写成整件物理成本(实付123 背 3305), 但利润口径
(physical_cost)早已片段封顶成实付×85% → 20 条全假。改: 判据/报警金额/自动关闭都用引擎成本。
"""
from decimal import Decimal

from app.models.order import Order
from app.services import data_quality_service as dq


def test_fragment_with_inflated_theoretical_not_flagged(db_session):
    """片段单: theoretical 整件化(3305)但引擎封顶85(104.55) → 不再报警。"""
    o = Order(platform="淘宝", order_no="CE1", status="signed", is_refill=False,
              paid_amount=Decimal("123"), theoretical_cost=Decimal("3305.81"),
              product_name="畔色北欧实木餐边柜", qty=1)
    db_session.add(o)
    db_session.commit()
    assert dq._cost_exceeds_paid_qualifies(o, db_session) is False


def test_genuine_mismatch_still_flagged(db_session):
    """真错配(引擎口径存在域): 实付≥成本一半(不触片段封顶)但成本>实付×1.5 的真亏本单 → 仍报警。"""
    o = Order(platform="淘宝", order_no="CE2", status="signed", is_refill=False,
              paid_amount=Decimal("1000"), actual_cost=Decimal("1700"),
              product_name="畔色实木餐边柜", qty=1)
    db_session.add(o)
    db_session.commit()
    assert dq._cost_exceeds_paid_qualifies(o, db_session) is True


def test_addon_ruled_order_not_flagged(db_session):
    """追加归主单裁定单(引擎成本0) → 不报警(此前 theoretical 2116 会报)。"""
    main = Order(platform="淘宝", order_no="CE3M", customer_phone="18499990000-1",
                 paid_amount=Decimal("9000"), status="signed", is_refill=False,
                 product_name="畔色玻璃门餐边柜", qty=1)
    addon = Order(platform="淘宝", order_no="CE3A", customer_phone="18499990000-1",
                  paid_amount=Decimal("516"), remark="追加推拉移门超白",
                  theoretical_cost=Decimal("2116.82"), status="signed", is_refill=False,
                  product_name="畔色玻璃门餐边柜", qty=1)
    db_session.add_all([main, addon])
    db_session.commit()
    assert dq._cost_exceeds_paid_qualifies(addon, db_session) is False
