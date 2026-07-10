# -*- coding: utf-8 -*-
"""专链/差价单有真实工厂账单 → 按实际入账不归零 (2026-07-10, 实测 …95421412 被归零利润虚高95%)。
(zz_ 前缀绕开既有 SQLite 连接污染排序坑。)"""
from decimal import Decimal as D

from app.models.order import Order
from app.services import order_financials as ofin


def test_speciallink_with_factory_bill_uses_actuals(db_session):
    """差价专链单 + 工厂账单1280 + 实际打包/物流 → 成本=实际合计, 不归零。"""
    o = Order(platform="淘宝", order_no="SL1", status="signed",
              product_name="畔色木作 差价邮费补拍专链", paid_amount=D("2680"),
              actual_cost=D("1280"), actual_packing=D("100"), actual_logistics=D("200"))
    db_session.add(o); db_session.commit()
    pb = ofin.physical_cost_breakdown(o, db_session)
    assert pb["cap_mode"] == "专链实账"
    assert D(str(pb["final"])) == D("1580")     # 1280+100+200, 不带 est 脏值


def test_speciallink_without_bill_still_zero(db_session):
    """对照: 无工厂账单的差价专链单照旧归零(防脏估值)。"""
    o = Order(platform="淘宝", order_no="SL2", status="signed",
              product_name="畔色木作 差价邮费补拍专链", paid_amount=D("50"),
              est_packing=D("170"), est_logistics=D("440"))
    db_session.add(o); db_session.commit()
    pb = ofin.physical_cost_breakdown(o, db_session)
    assert pb["cap_mode"] == "非产品归零"
    assert D(str(pb["final"])) == D("0")
