"""工厂下单表 GET /api/factory-orders 产品模糊搜索 (用户 2026-07-05)。

三供应链页统一支持「像产品总表/定价表」的产品名/SKU/产品编码模糊搜索;
本文件锁工厂下单表这一页的端点级行为(复用 factory_settlement_service._apply_product_search)。
"""
from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models import Base
from app.models.order import FactoryOrder
from app.services import auth_service


def _client():
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
    s.add_all([
        FactoryOrder(factory_order_no="F1", factory_name="木作厂", order_date=date(2026, 5, 3),
                     product_name="榉木岩板餐桌", sku="榉木-1.8米", product_code="PPS24210070901"),
        FactoryOrder(factory_order_no="F2", factory_name="木作厂", order_date=date(2026, 5, 4),
                     product_name="樱桃木窄柜", sku="樱桃-窄柜", product_code="PPS99"),
    ])
    s.commit()
    s.close()

    def override_get_db():
        ses = Sess()
        try:
            yield ses
        finally:
            ses.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app), token


def _nos(payload):
    return {r["factory_order_no"] for r in payload["rows"]}


def test_list_no_search_returns_all():
    client, token = _client()
    try:
        r = client.get("/api/factory-orders", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert _nos(r.json()) == {"F1", "F2"}
    finally:
        app.dependency_overrides.clear()


def test_search_by_product_name():
    client, token = _client()
    try:
        r = client.get("/api/factory-orders", params={"product_search": "岩板"},
                       headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert _nos(r.json()) == {"F1"}
    finally:
        app.dependency_overrides.clear()


def test_search_by_sku():
    client, token = _client()
    try:
        r = client.get("/api/factory-orders", params={"product_search": "窄柜"},
                       headers={"Authorization": f"Bearer {token}"})
        assert _nos(r.json()) == {"F2"}
    finally:
        app.dependency_overrides.clear()


def test_search_by_product_code():
    client, token = _client()
    try:
        r = client.get("/api/factory-orders", params={"product_search": "PPS24210070901"},
                       headers={"Authorization": f"Bearer {token}"})
        assert _nos(r.json()) == {"F1"}
    finally:
        app.dependency_overrides.clear()


def test_blank_search_ignored():
    client, token = _client()
    try:
        r = client.get("/api/factory-orders", params={"product_search": "   "},
                       headers={"Authorization": f"Bearer {token}"})
        assert _nos(r.json()) == {"F1", "F2"}
    finally:
        app.dependency_overrides.clear()
