# -*- coding: utf-8 -*-
"""定制追加归主单 (用户拍板 2026-07-14): 备注含「追加」定制语义(纯插座除外)
+ 同客户另有实付更大的主订单 → 补拍链接成本归零, 成本记在主单工厂账单。
案例 …35486210「追加上柜定制」¥2480(主单 …43486210 账单¥7600 已含上柜), 曾被算 ¥1871。
"""
from decimal import Decimal

from app.models.order import Order
from app.services import order_financials as ofin


def _pair(db, addon_remark="追加上柜定制", addon_paid="2480", main_paid="12164.52"):
    main = Order(platform="淘宝", order_no="MAIN1", customer_phone="18400000000-1111",
                 paid_amount=Decimal(main_paid), actual_cost=Decimal("7600"), status="signed")
    addon = Order(platform="淘宝", order_no="ADDON1", customer_phone="18400000000-1111",
                  paid_amount=Decimal(addon_paid), remark=addon_remark,
                  actual_cost=Decimal("0"), est_parts=Decimal("1871.05"), status="signed")
    db.add_all([main, addon])
    db.commit()
    return addon


def test_addon_with_main_zeroed(db_session):
    addon = _pair(db_session)
    pb = ofin.physical_cost_breakdown(addon, db_session)
    assert pb["cap_mode"] == "定制追加归主单"
    assert pb["final"] == Decimal("0")


def test_addon_without_main_not_zeroed(db_session):
    """同客户没有更大主单 → 不归零(照常成本), 防止误杀独立小单。"""
    o = Order(platform="淘宝", order_no="ALONE1", customer_phone="18411111111-2222",
              paid_amount=Decimal("2480"), remark="追加上柜定制",
              actual_cost=Decimal("0"), est_parts=Decimal("1871.05"), status="signed")
    db_session.add(o)
    db_session.commit()
    pb = ofin.physical_cost_breakdown(o, db_session)
    assert pb["cap_mode"] != "定制追加归主单"
    assert pb["final"] > 0


def test_socket_addon_excluded(db_session):
    """追加插座 → 不进归主单分支(插座成本口径另有单一真源)。"""
    addon = _pair(db_session, addon_remark="追加插座两个")
    pb = ofin.physical_cost_breakdown(addon, db_session)
    assert pb["cap_mode"] != "定制追加归主单"


def test_no_db_keeps_old_behavior(db_session):
    """db=None 的调用点(旧口径)不受影响。"""
    addon = _pair(db_session)
    pb = ofin.physical_cost_breakdown(addon)   # 不传 db
    assert pb["cap_mode"] != "定制追加归主单"
