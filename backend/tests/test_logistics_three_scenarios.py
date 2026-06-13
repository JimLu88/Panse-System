# -*- coding: utf-8 -*-
"""Plan C7: 物流追踪三场景集成测试 (monkeypatch 物流查询, 不出网)。

按系统真实联动语义断言:
  ① 订单: 在途 → paid 变 shipped; 签收 → 变 signed + tracking_confirmed
  ② 退货 (after_sales_return): 签收 → second_inbound_confirmed='是'
     (实际入库 +1 是用户在售后页点「二次确认入库」的人工动作, 不自动)
  ③ 配件行 (OrderAccessoryItem): 签收 → status='已到货' + 预警清除
"""
from decimal import Decimal

import pytest

from app.models.marketing import AfterSales
from app.models.order import Order, OrderAccessoryItem
from app.models.shipment import Shipment
from app.services.logistics_tracking_service import TrackEvent, TrackResult


def _track_result(no: str, *, signed: bool) -> TrackResult:
    return TrackResult(
        carrier_code="shunfeng", carrier_name="顺丰", tracking_no=no,
        state="3" if signed else "0",
        mapped_status="已到货" if signed else "运输中",
        is_signed=signed, provider="kuaidi100",
        events=[TrackEvent(time="2026-06-10 10:00", context="已签收" if signed else "运输中")],
    )


class TestScenario1OrderSigned:
    def test_in_transit_then_signed(self, db_session, monkeypatch):
        from app.services import shipment_service
        o = Order(platform="淘宝", order_no="C7-O1", status="paid",
                  tracking_no="SF-C7-001")
        db_session.add(o)
        db_session.flush()
        sh = Shipment(entity_type="order", entity_id=o.id,
                      tracking_no="SF-C7-001", active=True)
        db_session.add(sh)
        db_session.flush()

        # 第一查: 在途 → paid 变 shipped
        monkeypatch.setattr(shipment_service.lts, "query",
                            lambda db, no, cc=None: _track_result(no, signed=False))
        r1 = shipment_service.refresh_shipment(db_session, sh)
        assert r1["ok"] and not r1["signed"]
        assert o.status == "shipped"

        # 第二查: 签收 → 变 signed + tracking_confirmed + 停止轮询
        monkeypatch.setattr(shipment_service.lts, "query",
                            lambda db, no, cc=None: _track_result(no, signed=True))
        r2 = shipment_service.refresh_shipment(db_session, sh)
        assert r2["ok"] and r2["signed"]
        assert o.status == "signed"
        assert o.tracking_confirmed is True
        assert sh.active is False


class TestScenario2ReturnSigned:
    def test_return_signed_marks_inbound_confirm(self, db_session, monkeypatch):
        from app.services import shipment_service
        a = AfterSales(platform_order_no="C7-R1", return_tracking_no="SF-C7-002")
        db_session.add(a)
        db_session.flush()
        sh = Shipment(entity_type="after_sales_return", entity_id=a.id,
                      tracking_no="SF-C7-002", active=True)
        db_session.add(sh)
        db_session.flush()

        monkeypatch.setattr(shipment_service.lts, "query",
                            lambda db, no, cc=None: _track_result(no, signed=True))
        r = shipment_service.refresh_shipment(db_session, sh)
        assert r["ok"] and r["signed"]
        assert a.second_inbound_confirmed == "是"


class TestScenario3AccessoryArrived:
    def test_accessory_signed_marks_arrived(self, db_session, monkeypatch):
        from app.services import logistics_tracking_service as lts_mod
        o = Order(platform="淘宝", order_no="C7-A1", status="paid")
        db_session.add(o)
        db_session.flush()
        item = OrderAccessoryItem(
            order_id=o.id, order_no="C7-A1", material_code="AC-C7",
            material_name="测试配件C7", qty_required=Decimal("2"),
            status="运输中", tracking_no="SF-C7-003",
            alert_level="warn", alert_reason="超期未到",
        )
        db_session.add(item)
        db_session.flush()

        monkeypatch.setattr(lts_mod, "query",
                            lambda db, no, cc=None: _track_result(no, signed=True))
        r = lts_mod.refresh_item(db_session, item.id)
        assert r["ok"]
        assert item.status == "已到货"
        assert item.alert_level is None and item.alert_reason is None
