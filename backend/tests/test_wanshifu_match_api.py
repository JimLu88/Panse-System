"""PATCH /api/finance/wanshifu-orders/{id}: 人工逐行确认匹配 (方案2)。

覆盖: 命中订单库→manual+in_lib / 订单库暂无→记匹配+批注 / 同号 unchanged + 清空 /
404 + 未认证拒绝。realtime_sync 已 patch 掉避免重算副作用。
"""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models import Base
from app.models.finance import WanshifuOrder
from app.models.order import Order
from app.services import auth_service

_NO = "3210721863514837177"


def _setup():
    engine = create_engine(
        "sqlite:///:memory:", future=True,
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Sess = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    s = Sess()
    admin = auth_service.create_user(s, username="admin", password="x", role="admin",
                                     display_name="管理员")
    s.commit()
    token = auth_service.create_token(user_id=admin.id, username=admin.username, role="admin")
    w = WanshifuOrder(wsf_order_no="P900", customer_name="测试客户")
    s.add(w)
    s.add(Order(platform="淘宝", order_no=_NO, qty=1, customer_name="c",
                order_date=date(2026, 6, 1)))
    s.commit()
    wid = w.id
    s.close()

    def override_get_db():
        ses = Sess()
        try:
            yield ses
        finally:
            ses.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app), token, wid


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_patch_sets_manual_match_when_order_in_lib():
    client, token, wid = _setup()
    try:
        with patch("app.services.realtime_sync_service.trigger"):
            r = client.patch(f"/api/finance/wanshifu-orders/{wid}",
                             json={"matched_order_no": _NO}, headers=_auth(token))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["matched_order_no"] == _NO
        assert body["in_lib"] is True
    finally:
        app.dependency_overrides.clear()


def test_patch_records_absent_order():
    client, token, wid = _setup()
    try:
        with patch("app.services.realtime_sync_service.trigger"):
            r = client.patch(f"/api/finance/wanshifu-orders/{wid}",
                             json={"matched_order_no": "9999999999999999999"},
                             headers=_auth(token))
        assert r.status_code == 200, r.text
        assert r.json()["in_lib"] is False
    finally:
        app.dependency_overrides.clear()


def test_patch_unchanged_then_clear():
    client, token, wid = _setup()
    try:
        with patch("app.services.realtime_sync_service.trigger"):
            client.patch(f"/api/finance/wanshifu-orders/{wid}",
                         json={"matched_order_no": _NO}, headers=_auth(token))
            r2 = client.patch(f"/api/finance/wanshifu-orders/{wid}",
                              json={"matched_order_no": _NO}, headers=_auth(token))
            assert r2.json().get("unchanged") is True
            r3 = client.patch(f"/api/finance/wanshifu-orders/{wid}",
                              json={"matched_order_no": ""}, headers=_auth(token))
            assert r3.json()["matched_order_no"] is None
    finally:
        app.dependency_overrides.clear()


def test_patch_404_for_missing_and_rejects_unauthenticated():
    client, token, wid = _setup()
    try:
        with patch("app.services.realtime_sync_service.trigger"):
            r = client.patch("/api/finance/wanshifu-orders/999999",
                             json={"matched_order_no": "1"}, headers=_auth(token))
            assert r.status_code == 404
        r2 = client.patch(f"/api/finance/wanshifu-orders/{wid}",
                          json={"matched_order_no": "1"})
        assert r2.status_code in (401, 403)
    finally:
        app.dependency_overrides.clear()
