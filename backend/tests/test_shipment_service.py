"""shipments 中央物流追踪 — 服务层测试。

自带内存 sqlite + mock provider 查询 (不连真网/真 key)。验证:
派生回写 (订单签收/在途、售后返厂二次入库)、sync 幂等、未配置跳过。
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401  注册所有模型 (含 Shipment) 到 metadata
from app.models.base import Base
from app.models.order import Order
from app.models.marketing import AfterSales
from app.models.shipment import Shipment
from app.services import logistics_tracking_service as lts
from app.services import shipment_service as ss


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


class _Track:
    """伪 TrackResult (只用到 shipment_service 读取的字段)。"""
    def __init__(self, signed, status, events=None):
        self.is_signed = signed
        self.mapped_status = status
        self.provider = "kdniao"
        self.carrier_code = "SF"
        self.carrier_name = "顺丰"
        self.state = "3" if signed else "2"
        self.events = events or []


def _patch(monkeypatch, result):
    monkeypatch.setattr(lts, "is_configured", lambda db: True)
    monkeypatch.setattr(lts, "query", lambda db, no, code=None: result)


def test_order_signed_sets_status_and_confirmed(monkeypatch, db):
    o = Order(platform="淘宝", order_no="T1", status="paid", tracking_no="SF111")
    db.add(o)
    db.commit()
    ss.sync_all(db)
    _patch(monkeypatch, _Track(signed=True, status="已到货"))
    ss.refresh_active(db)
    db.refresh(o)
    assert o.status == "signed"
    assert o.tracking_confirmed is True
    sh = db.query(Shipment).filter_by(entity_type="order", entity_id=o.id).one()
    assert sh.is_signed is True and sh.active is False


def test_order_in_transit_sets_shipped(monkeypatch, db):
    o = Order(platform="淘宝", order_no="T2", status="paid", tracking_no="SF222")
    db.add(o)
    db.commit()
    ss.sync_all(db)
    _patch(monkeypatch, _Track(signed=False, status="运输中"))
    ss.refresh_active(db)
    db.refresh(o)
    assert o.status == "shipped"


def test_after_sales_return_signed_sets_inbound(monkeypatch, db):
    a = AfterSales(platform_order_no="T3", return_tracking_no="SF333")
    db.add(a)
    db.commit()
    ss.sync_all(db)
    _patch(monkeypatch, _Track(signed=True, status="已到货"))
    ss.refresh_active(db)
    db.refresh(a)
    assert a.second_inbound_confirmed == "是"


def test_sync_is_idempotent(db):
    o = Order(platform="淘宝", order_no="T4", status="paid", tracking_no="SF444")
    db.add(o)
    db.commit()
    assert ss.sync_all(db)["created"] == 1
    assert ss.sync_all(db)["created"] == 0  # 第二次不重复创建


def test_unconfigured_skips(monkeypatch, db):
    monkeypatch.setattr(lts, "is_configured", lambda db: False)
    out = ss.refresh_active(db)
    assert out.get("skipped")


def test_refresh_entity_no_tracking_returns_error(db):
    o = Order(platform="淘宝", order_no="T5", status="paid")  # 无单号
    db.add(o)
    db.commit()
    out = ss.refresh_entity(db, "order", o.id)
    assert out["ok"] is False


def test_signed_order_not_auto_tracked(db):
    o = Order(platform="淘宝", order_no="C1", status="signed", tracking_no="SF900")
    db.add(o)
    db.commit()
    r = ss.sync_all(db)
    assert r["created"] == 0          # 已成功(已签收)订单不建自动追踪
    assert db.query(Shipment).count() == 0


def test_cancelled_order_not_auto_tracked(db):
    o = Order(platform="淘宝", order_no="C2", status="cancelled", tracking_no="SF901")
    db.add(o)
    db.commit()
    assert ss.sync_all(db)["created"] == 0


def test_sync_deactivates_when_order_turns_terminal(db):
    o = Order(platform="淘宝", order_no="C3", status="paid", tracking_no="SF902")
    db.add(o)
    db.commit()
    ss.sync_all(db)                   # paid → 建 active 追踪
    sh = db.query(Shipment).filter_by(entity_type="order", entity_id=o.id).one()
    assert sh.active is True
    o.status = "cancelled"           # 订单被关闭
    db.commit()
    r = ss.sync_all(db)
    db.refresh(sh)
    assert sh.active is False         # reconcile 停止自动轮询
    assert r["deactivated"] == 1


def test_manual_refresh_on_signed_order_queries_but_stays_inactive(monkeypatch, db):
    o = Order(platform="淘宝", order_no="C4", status="signed", tracking_no="SF903")
    db.add(o)
    db.commit()
    _patch(monkeypatch, _Track(signed=False, status="运输中"))
    r = ss.refresh_entity(db, "order", o.id)   # 手动强制查
    assert r["ok"] is True                       # 已成功订单手动仍可查
    sh = db.query(Shipment).filter_by(entity_type="order", entity_id=o.id).one()
    assert sh.active is False                     # 但不进自动轮询队列
