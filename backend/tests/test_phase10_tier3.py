"""Phase 10 Tier 3: 全局搜索 / Webhook / 智能定价 / 异常诊断."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models import Base
from app.models.bom import BomLine
from app.models.exception import DataException
from app.models.inventory import ProductInventory
from app.models.material import Material
from app.models.order import Order
from app.services import (
    auth_service,
    exception_diagnosis_service,
    smart_pricing_service,
    webhook_service,
)
from app.services.ai_provider import AiResponse


def _client():
    engine = create_engine("sqlite:///:memory:", future=True,
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Sess = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    s = Sess()
    admin = auth_service.create_user(s, username="admin", password="x", role="admin",
                                     display_name="A")
    s.commit()
    token = auth_service.create_token(user_id=admin.id, username=admin.username, role="admin")
    s.close()
    def override():
        ses = Sess()
        try: yield ses
        finally: ses.close()
    app.dependency_overrides[get_db] = override
    return TestClient(app), token, Sess


# ============================ 全局搜索 ============================ #


def test_search_finds_orders():
    client, token, Sess = _client()
    h = {"Authorization": f"Bearer {token}"}
    try:
        s = Sess()
        s.add(Order(platform="淘宝", order_no="S001", qty=1, status="paid",
                     customer_name="张三", customer_phone="13800001111"))
        s.commit(); s.close()
        r = client.get("/api/search?q=S001", headers=h)
        assert r.status_code == 200, r.text
        hits = r.json()
        assert any(h["kind"] == "order" for h in hits)
    finally:
        app.dependency_overrides.clear()


def test_search_finds_by_phone():
    client, token, Sess = _client()
    h = {"Authorization": f"Bearer {token}"}
    try:
        s = Sess()
        s.add(Order(platform="淘宝", order_no="S002", qty=1, status="paid",
                     customer_phone="13900009999"))
        s.commit(); s.close()
        r = client.get("/api/search?q=13900009999", headers=h)
        hits = r.json()
        assert any(h["kind"] == "order" for h in hits)
    finally:
        app.dependency_overrides.clear()


def test_search_empty_q_returns_empty():
    client, token, _ = _client()
    h = {"Authorization": f"Bearer {token}"}
    try:
        r = client.get("/api/search?q=nonexistentxxxx", headers=h)
        assert r.status_code == 200
        assert r.json() == []
    finally:
        app.dependency_overrides.clear()


# ============================ 智能定价 ============================ #


def test_smart_pricing_with_history_and_cost(db_session):
    db_session.add_all([
        Material(code="M1", name="x", price=Decimal("100")),
        BomLine(product_code="P1", material_code="M1", qty_per_product=Decimal("2")),
    ])
    today = date.today()
    db_session.add(Order(platform="淘宝", order_no="O1", order_date=today,
                          product_code="P1", qty=1,
                          paid_amount=Decimal("500"), status="shipped"))
    db_session.flush()
    s = smart_pricing_service.suggest_price(db_session, product_code="P1",
                                             target_margin=0.4)
    assert s.cost == 200.0
    assert s.historical_avg_price == 500.0
    assert s.suggested_price > 0
    assert s.inventory_pressure == 1.0   # 无库存压力


def test_smart_pricing_inventory_pressure(db_session):
    db_session.add_all([
        Material(code="M1", name="x", price=Decimal("10")),
        BomLine(product_code="P1", material_code="M1", qty_per_product=Decimal("1")),
        ProductInventory(warehouse="default", product_code="P1",
                          physical_qty=Decimal("100")),
    ])
    # 造历史销量, forecast 应该不为 0
    today = date.today()
    from datetime import timedelta
    for i in range(20):
        db_session.add(Order(platform="淘宝", order_no=f"P{i}",
                              order_date=today - timedelta(days=i),
                              product_code="P1", sku_code="S1",
                              qty=1, paid_amount=Decimal("50"), status="shipped"))
    db_session.flush()
    s = smart_pricing_service.suggest_price(db_session, product_code="P1")
    # 库存 100, 预测有限 → 压力应触发降价
    assert s.inventory_pressure <= 1.0


def test_smart_pricing_no_data(db_session):
    s = smart_pricing_service.suggest_price(db_session, product_code="NONE")
    assert s.cost == 0
    assert s.historical_avg_price == 0


# ============================ 异常诊断 ============================ #


def test_diagnose_returns_text_when_ai_unavailable(db_session):
    exc = DataException(
        source_table="orders", source_pk="O1",
        exception_type="cost_anomaly", severity="warning",
        description="成本异常",
    )
    db_session.add(exc); db_session.flush()
    r = exception_diagnosis_service.diagnose(db_session, exc.id)
    assert "AI" in r["analysis"] or r["analysis"]


def test_diagnose_with_ai(db_session):
    exc = DataException(
        source_table="orders", source_pk="O1",
        exception_type="cost_anomaly", severity="warning",
        description="成本异常",
    )
    db_session.add(exc); db_session.flush()
    fake = '{"analysis":"原因是物料涨价","suggested_actions":[],"severity_recommended":"error"}'
    class P:
        name, model = "x", "x"
        def chat(self, **kw):
            return AiResponse(text=fake, model="x")
    with patch("app.services.exception_diagnosis_service.build_provider",
               return_value=P()):
        r = exception_diagnosis_service.diagnose(db_session, exc.id)
    assert "涨价" in r["analysis"]
    assert r["severity_recommended"] == "error"


# ============================ Webhook ============================ #


def test_webhook_no_endpoints(db_session):
    r = webhook_service.publish(db_session, event="order.paid", payload={"id": 1})
    assert r["delivered"] == 0


def test_webhook_with_endpoints(db_session):
    webhook_service.set_endpoints(db_session, [
        {"url": "https://example.com/h", "secret": "s",
         "events": ["order.paid", "order.shipped"]},
    ])
    db_session.commit()
    r = webhook_service.publish(db_session, event="order.paid", payload={"id": 1})
    assert r["delivered"] == 1
    # event 不在订阅范围 → 0
    r2 = webhook_service.publish(db_session, event="order.cancelled", payload={})
    assert r2["delivered"] == 0


def test_webhook_sign_changes_per_payload(db_session):
    s1 = webhook_service._sign({"event": "x", "data": {"a": 1}}, "secret")
    s2 = webhook_service._sign({"event": "x", "data": {"a": 2}}, "secret")
    assert s1 != s2
