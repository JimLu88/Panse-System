# -*- coding: utf-8 -*-
"""订单状态规范化(中文/遗留→枚举) + 状态机容错 + 批量回填 测试。"""
from app.models.order import Order
from app.services import order_service as S


def test_normalize_status_map():
    assert S.normalize_status("等待买家付款") == "pending_payment"
    assert S.normalize_status("买家已付款,等待卖家发货") == "paid"
    assert S.normalize_status("卖家已发货，等待买家确认") == "shipped"
    assert S.normalize_status("交易成功") == "signed"
    assert S.normalize_status("completed") == "signed"
    assert S.normalize_status("confirmed") == "signed"
    assert S.normalize_status("交易关闭") == "cancelled"
    assert S.normalize_status("paid") == "paid"          # 已是枚举原样
    assert S.normalize_status("某种没见过的状态") == "某种没见过的状态"  # 未知原样保留


def test_transition_tolerates_chinese_status(db_session):
    """看板报错复现修复: 中文「等待买家付款」→ paid 应成功(先规范化)。"""
    o = Order(platform="淘宝", order_no="K1", status="等待买家付款", is_historical=True)
    db_session.add(o)
    db_session.flush()
    S.transition(db_session, o, "paid", actor="t", auto_factory=False)
    assert o.status == "paid"


def test_normalize_all_statuses_backfill(db_session):
    db_session.add(Order(platform="淘宝", order_no="A", status="等待买家付款"))
    db_session.add(Order(platform="淘宝", order_no="B", status="交易成功"))
    db_session.add(Order(platform="淘宝", order_no="C", status="paid"))   # 已枚举不动
    db_session.flush()
    r = S.normalize_all_statuses(db_session)
    assert r["fixed"] == 2 and r["scanned"] == 3
    got = {o.order_no: o.status for o in db_session.query(Order).all()}
    assert got == {"A": "pending_payment", "B": "signed", "C": "paid"}
