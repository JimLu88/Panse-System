"""Phase 3: 千牛订单截图 / 进货单截图 OCR + commit."""
from __future__ import annotations

import io
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models import Base
from app.models.order import Order, PartPurchase
from app.services import auth_service, vision_ocr_service
from app.services.ai_provider import AiResponse, AiUnavailable


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


_FAKE_QIANNIU = """{
  "orders": [
    {
      "order_no": "T123456",
      "platform": "淘宝",
      "order_date": "2026-05-21",
      "customer_name": "张三",
      "customer_phone": "13800001111",
      "customer_address": "北京市朝阳区...",
      "product_name": "电视柜",
      "sku": "胡桃色1.8米",
      "qty": 1,
      "paid_amount": 2380,
      "platform_fee": 142.8,
      "remark": "周六送",
      "confidence": 0.92
    }
  ],
  "ocr_warnings": []
}"""

_FAKE_PURCHASE = """{
  "purchase": {
    "supplier_name": "木作工厂",
    "purchase_date": "2026-05-15",
    "tracking_no": "SF12345",
    "carrier": "顺丰",
    "lines": [
      {"material_name": "木方", "qty": 10, "unit": "件", "unit_price": 25, "amount": 250},
      {"material_name": "五金", "qty": 5, "unit": "套", "unit_price": 18}
    ],
    "total_amount": 340,
    "warnings": []
  }
}"""


def _fake_provider_qianniu():
    class P:
        name = "anthropic"
        model = "claude-opus-4-7"
        def chat_with_image(self, **kw):
            return AiResponse(text=_FAKE_QIANNIU, model="x")
    return P()


def _fake_provider_purchase():
    class P:
        name = "anthropic"
        model = "claude-opus-4-7"
        def chat_with_image(self, **kw):
            return AiResponse(text=_FAKE_PURCHASE, model="x")
    return P()


# ----------------------------- 服务层 --------------------------- #


def test_parse_qianniu_returns_orders(db_session):
    with patch("app.services.vision_ocr_service.build_provider",
               return_value=_fake_provider_qianniu()):
        data = vision_ocr_service.parse_qianniu_order(
            db_session, b"\x89PNG fake bytes", mime="image/png",
        )
    assert len(data["orders"]) == 1
    o = data["orders"][0]
    assert o["order_no"] == "T123456"
    assert o["paid_amount"] == 2380


def test_parse_purchase_returns_lines(db_session):
    with patch("app.services.vision_ocr_service.build_provider",
               return_value=_fake_provider_purchase()):
        data = vision_ocr_service.parse_purchase_invoice(
            db_session, b"\x89PNG", mime="image/png",
        )
    p = data["purchase"]
    assert p["supplier_name"] == "木作工厂"
    assert len(p["lines"]) == 2


def test_parse_qianniu_raises_when_no_ai(db_session):
    """未配 OCR 时, parse 抛 AiUnavailable."""
    import pytest
    with pytest.raises(AiUnavailable):
        vision_ocr_service.parse_qianniu_order(db_session, b"x", mime="image/png")


def test_parse_qianniu_handles_garbage_response(db_session):
    """AI 返回不是 JSON → AiUnavailable."""
    import pytest
    class P:
        name, model = "x", "x"
        def chat_with_image(self, **kw):
            return AiResponse(text="not json at all", model="x")
    with patch("app.services.vision_ocr_service.build_provider", return_value=P()):
        with pytest.raises(AiUnavailable):
            vision_ocr_service.parse_qianniu_order(db_session, b"x", mime="image/png")


# ----------------------------- API 端到端 ---------------------- #


def test_qianniu_parse_endpoint_no_ai_returns_503():
    client, token, _ = _client()
    h = {"Authorization": f"Bearer {token}"}
    try:
        r = client.post("/api/screenshots/qianniu-orders/parse", headers=h,
                        files={"file": ("x.png", b"\x89PNG fake", "image/png")})
        assert r.status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_qianniu_parse_with_ai_works():
    client, token, _ = _client()
    h = {"Authorization": f"Bearer {token}"}
    try:
        with patch("app.services.vision_ocr_service.build_provider",
                   return_value=_fake_provider_qianniu()):
            r = client.post("/api/screenshots/qianniu-orders/parse", headers=h,
                            files={"file": ("x.png", b"\x89PNG fake", "image/png")})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["image_b64"]
        assert len(body["orders"]) == 1
    finally:
        app.dependency_overrides.clear()


def test_qianniu_commit_inserts_orders():
    client, token, Sess = _client()
    h = {"Authorization": f"Bearer {token}"}
    try:
        r = client.post("/api/screenshots/qianniu-orders/commit", headers=h, json={
            "orders": [{
                "order_no": "C001",
                "platform": "淘宝",
                "customer_name": "李四",
                "qty": 2,
                "paid_amount": 999.5,
            }],
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["inserted"] == 1
        # 重复入应被去重 (相同数据 → skipped; 若字段不同则会进 conflicts, 见下)
        r = client.post("/api/screenshots/qianniu-orders/commit", headers=h, json={
            "orders": [{"order_no": "C001", "qty": 2}],
        })
        body = r.json()
        assert body["inserted"] == 0
        assert "C001" in body["skipped_existing"]
        # 字段不同的重复 → 记为冲突 (待人工裁决), 不静默覆盖
        r = client.post("/api/screenshots/qianniu-orders/commit", headers=h, json={
            "orders": [{"order_no": "C001", "qty": 9}],
        })
        body = r.json()
        assert body["inserted"] == 0
        assert "C001" in body["conflicts"]
        # DB 检查
        s = Sess()
        try:
            o = s.execute(select(Order).where(Order.order_no == "C001")).scalar_one()
            assert o.customer_name == "李四"
        finally:
            s.close()
    finally:
        app.dependency_overrides.clear()


def test_purchase_parse_and_commit():
    client, token, Sess = _client()
    h = {"Authorization": f"Bearer {token}"}
    try:
        with patch("app.services.vision_ocr_service.build_provider",
                   return_value=_fake_provider_purchase()):
            r = client.post("/api/screenshots/purchase/parse", headers=h,
                            files={"file": ("x.png", b"\x89PNG", "image/png")})
        assert r.status_code == 200
        # 用户在 UI 调好后 commit
        r = client.post("/api/screenshots/purchase/commit", headers=h, json={
            "supplier": "木作工厂",
            "purchase_date": "2026-05-15",
            "tracking_no": "SF12345",
            "lines": [
                {"material_name": "木方", "qty": 10, "unit_price": 25, "amount": 250},
            ],
        })
        assert r.status_code == 200
        body = r.json()
        assert body["inserted"] == 1
        assert body["has_tracking"] is True
        s = Sess()
        try:
            pp = s.execute(select(PartPurchase).where(
                PartPurchase.supplier == "木作工厂"
            )).scalar_one()
            assert pp.tracking_no == "SF12345"
        finally:
            s.close()
    finally:
        app.dependency_overrides.clear()


def test_oversize_image_rejected():
    client, token, _ = _client()
    h = {"Authorization": f"Bearer {token}"}
    try:
        big = b"X" * (21 * 1024 * 1024)
        r = client.post("/api/screenshots/qianniu-orders/parse", headers=h,
                        files={"file": ("big.png", big, "image/png")})
        assert r.status_code == 413
    finally:
        app.dependency_overrides.clear()


def test_non_image_rejected():
    client, token, _ = _client()
    h = {"Authorization": f"Bearer {token}"}
    try:
        r = client.post("/api/screenshots/qianniu-orders/parse", headers=h,
                        files={"file": ("x.txt", b"text content", "text/plain")})
        assert r.status_code == 400
    finally:
        app.dependency_overrides.clear()
