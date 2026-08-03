"""远期单序号 (工厂看板口径, 含日期式延期) + factory_label 显示 + assign_remote_seqs (2026-07-09)。"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.models.order import Order
from app.services import order_flags as of
from app.services import order_sheet_archive_service as osa

TODAY = date(2026, 7, 9)


def test_is_factory_remote_keyword():
    o = Order(order_no="R1", platform="淘宝", seller_memo="等客户通知再发")
    assert of.is_factory_remote(o, TODAY) is True


def test_is_factory_remote_far_future_date():
    # 10月6日发货 距 7-9 约89天 > 30天工期 → 太早别做 = 远期 (日期式延期, 无关键词)
    o = Order(order_no="R2", platform="淘宝", seller_memo="10月6日发货", order_date=date(2026, 6, 16))
    assert of.is_factory_remote(o, TODAY) is True


def test_is_factory_remote_near_date_not_remote():
    # 7月15日发货 距今6天 < 工期 → 该排产, 不算远期
    o = Order(order_no="R3", platform="淘宝", seller_memo="7月15日发货", order_date=date(2026, 7, 1))
    assert of.is_factory_remote(o, TODAY) is False


def test_activated_overrides_remote():
    o = Order(order_no="R4", platform="淘宝", seller_memo="10月发货 开始制作")
    assert of.is_factory_remote(o, TODAY) is False   # 激活优先级最高


def test_manual_remote_ship_flag():
    o = Order(order_no="R5", platform="淘宝", is_remote_ship=True, seller_memo="8月1日发货")
    assert of.is_factory_remote(o, TODAY) is True     # 手动标远期(近发货日也算)


def test_factory_label():
    assert of.factory_label(Order(order_no="L1", platform="淘宝", factory_no=285)) == "畔色285单"
    assert of.factory_label(Order(order_no="L2", platform="淘宝", remote_seq=3)) == "远期单3"
    assert of.factory_label(Order(order_no="L3", platform="淘宝")) == ""
    # 正式号优先于远期序号
    assert of.factory_label(Order(order_no="L4", platform="淘宝", factory_no=290, remote_seq=3)) == "畔色290单"


def test_assign_remote_seqs_idempotent(db_session):
    db_session.add(Order(order_no="A1", platform="淘宝", seller_memo="等通知", is_refill=False))
    db_session.add(Order(order_no="A2", platform="淘宝", seller_memo="定制樱桃木台面加75", is_refill=False))
    db_session.add(Order(
        order_no="A3", platform="淘宝", is_customer_delayed=True,
        customer_delay_deadline=date(2026, 10, 1), is_refill=False,
    ))
    db_session.flush()
    osa.assign_remote_seqs(db_session)
    a1 = db_session.execute(select(Order).where(Order.order_no == "A1")).scalar_one()
    a2 = db_session.execute(select(Order).where(Order.order_no == "A2")).scalar_one()
    a3 = db_session.execute(select(Order).where(Order.order_no == "A3")).scalar_one()
    assert a1.remote_seq is not None      # 远期 → 有序号
    assert a2.remote_seq is None          # 非远期 → 无序号
    assert a3.remote_seq is not None      # 无文字备注的客户延期单也必须有远期序号
    seq = a1.remote_seq
    osa.assign_remote_seqs(db_session)    # 再跑一次
    db_session.refresh(a1)
    assert a1.remote_seq == seq           # 幂等: 号不变
