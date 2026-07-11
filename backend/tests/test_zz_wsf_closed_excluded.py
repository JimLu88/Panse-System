# -*- coding: utf-8 -*-
"""万师傅交易关闭单排除 (2026-07-11 用户裁定): 关闭单不参与配对、不占淘宝订单。
实库状态串带后缀「交易关闭（自动关单）」→ 按包含"关闭"判, 等值判会漏网。
自愈: 先配上、重导后状态推进成关闭的旧配对每轮解绑(人工指定除外)。
(zz_ 前缀绕开既有 SQLite 连接污染排序坑。)"""
from decimal import Decimal as D

from app.models.finance import WanshifuOrder
from app.models.order import Order
from app.services import wanshifu_order_service as svc


def _tb(db, no, phone="13800000001"):
    db.add(Order(platform="淘宝", order_no=no, qty=1, status="signed",
                 paid_amount=D("3000"), customer_name="测试客", customer_phone=phone,
                 customer_address="浙江省 杭州市 西湖区 文一路1号"))


def test_closed_row_not_matched(db_session):
    """关闭单(带自动关单后缀)手机号能对上也不配, 标 closed。"""
    _tb(db_session, "T1")
    db_session.add(WanshifuOrder(wsf_order_no="W1", status="交易关闭（自动关单）",
                                 customer_phone="13800000001", customer_name="测试客"))
    db_session.flush()
    c = svc.match_orders(db_session)
    w = db_session.query(WanshifuOrder).filter_by(wsf_order_no="W1").one()
    assert w.matched_order_no is None
    assert w.match_method == "closed"
    assert c["closed"] == 1


def test_selfheal_unbinds_previously_matched_closed(db_session):
    """先配上、后来关闭的 → 每轮自愈解绑; 人工指定的也解绑(用户 2026-07-11:
    29笔人工校对配上的关闭单一并解绑, 关闭优先级最高), 原单号留批注可追溯。"""
    _tb(db_session, "T2")
    db_session.add(WanshifuOrder(wsf_order_no="W2", status="交易关闭（自动关单）",
                                 matched_order_no="T2", match_method="phone_full"))
    db_session.add(WanshifuOrder(wsf_order_no="W3", status="交易关闭（自动关单）",
                                 matched_order_no="T2", match_method="manual"))
    db_session.flush()
    c = svc.match_orders(db_session)
    w2 = db_session.query(WanshifuOrder).filter_by(wsf_order_no="W2").one()
    w3 = db_session.query(WanshifuOrder).filter_by(wsf_order_no="W3").one()
    assert w2.matched_order_no is None and w2.match_method == "closed"
    assert w3.matched_order_no is None and w3.match_method == "closed"
    assert "T2" in (w3.match_note or "")      # 原配对留痕
    assert c["closed_cleared"] == 2


def test_success_row_still_matches(db_session):
    """交易成功的照常配对(回归)。"""
    _tb(db_session, "T3", phone="13800000003")
    db_session.add(WanshifuOrder(wsf_order_no="W4", status="交易成功",
                                 customer_phone="13800000003", customer_name="测试客"))
    db_session.flush()
    svc.match_orders(db_session)
    w = db_session.query(WanshifuOrder).filter_by(wsf_order_no="W4").one()
    assert w.matched_order_no == "T3"
    assert w.match_method == "phone_full"


def test_is_closed_variants():
    assert svc._is_closed("交易关闭（自动关单）")
    assert svc._is_closed("交易关闭")
    assert not svc._is_closed("交易成功")
    assert not svc._is_closed("服务中（已上门待完成）")
    assert not svc._is_closed(None)