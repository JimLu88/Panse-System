"""/api/admin/integrations: GET / PUT / test 联通."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models import Base
from app.services import auth_service
from app.services.ai_provider import AiResponse


def _client_with_admin_token():
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
    operator = auth_service.create_user(s, username="op", password="x", role="operator",
                                        display_name="操作员")
    s.commit()
    op_token = auth_service.create_token(user_id=operator.id, username=operator.username,
                                         role="operator")
    s.close()

    def override_get_db():
        ses = Sess()
        try: yield ses
        finally: ses.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    return client, token, op_token


def test_get_integrations_requires_admin():
    client, _, op_token = _client_with_admin_token()
    try:
        r = client.get("/api/admin/integrations",
                       headers={"Authorization": f"Bearer {op_token}"})
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_put_then_get_roundtrip_masks_api_key():
    client, token, _ = _client_with_admin_token()
    try:
        r = client.put(
            "/api/admin/integrations",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "ocr": {
                    "provider": "openai",
                    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "api_key": "sk-test-very-secret-abcdef123456",
                    "model": "qwen-vl-max",
                },
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ocr"]["provider"] == "openai"
        assert body["ocr"]["model"] == "qwen-vl-max"
        assert body["ocr"]["api_key_set"] is True
        # api_key 必须脱敏: 不能含完整 key
        assert "sk-test-very-secret-abcdef123456" not in body["ocr"]["api_key_masked"]
        assert body["ocr"]["api_key_masked"].startswith("sk-")
        # 支持的 provider 列表
        assert any(p["value"] == "anthropic" for p in body["supported_providers"])
        assert any(p["value"] == "openai" for p in body["supported_providers"])

        # GET 也应该一致
        r2 = client.get("/api/admin/integrations",
                        headers={"Authorization": f"Bearer {token}"})
        assert r2.status_code == 200
        assert r2.json()["ocr"]["api_key_set"] is True
    finally:
        app.dependency_overrides.clear()


def test_clear_api_key():
    client, token, _ = _client_with_admin_token()
    try:
        client.put("/api/admin/integrations",
                   headers={"Authorization": f"Bearer {token}"},
                   json={"ocr": {"api_key": "k1", "provider": "openai", "model": "m"}})
        r = client.put("/api/admin/integrations",
                       headers={"Authorization": f"Bearer {token}"},
                       json={"ocr": {"api_key": "__CLEAR__"}})
        assert r.status_code == 200
        assert r.json()["ocr"]["api_key_set"] is False
    finally:
        app.dependency_overrides.clear()


def test_test_endpoint_uses_provider():
    client, token, _ = _client_with_admin_token()
    try:
        client.put("/api/admin/integrations",
                   headers={"Authorization": f"Bearer {token}"},
                   json={"ocr": {"provider": "openai", "api_key": "k",
                                 "model": "qwen-vl-max", "base_url": "https://x/v1"}})
        fake_provider = MagicMock()
        fake_provider.name = "openai"
        fake_provider.model = "qwen-vl-max"
        fake_provider.chat.return_value = AiResponse(text="好", model="qwen-vl-max")
        with patch("app.api.admin.build_provider", return_value=fake_provider):
            r = client.post(
                "/api/admin/integrations/test",
                headers={"Authorization": f"Bearer {token}"},
                json={"kind": "ocr"},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["sample"] == "好"
        assert body["provider"] == "openai"
    finally:
        app.dependency_overrides.clear()


def test_test_endpoint_returns_error_on_failure():
    client, token, _ = _client_with_admin_token()
    try:
        # 不配 key, build_provider 会抛 AiUnavailable
        r = client.post(
            "/api/admin/integrations/test",
            headers={"Authorization": f"Bearer {token}"},
            json={"kind": "ocr"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert body["error"]
    finally:
        app.dependency_overrides.clear()


# ----------------------------- 系统监控 / 看门狗 endpoints ----------- #


def test_system_status_endpoint_requires_admin():
    client, _, op_token = _client_with_admin_token()
    try:
        r = client.get("/api/admin/system-status",
                       headers={"Authorization": f"Bearer {op_token}"})
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_system_status_endpoint_returns_snapshot():
    client, token, _ = _client_with_admin_token()
    try:
        r = client.get("/api/admin/system-status",
                       headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["db_ok"] is True
        assert body["python_version"]
        assert body["uptime_sec"] >= 0
        assert isinstance(body["recent_checks"], list)
        names = {c["name"] for c in body["recent_checks"]}
        assert {"db_ping", "disk", "memory"} <= names
    finally:
        app.dependency_overrides.clear()


def test_health_logs_endpoint():
    client, token, _ = _client_with_admin_token()
    try:
        # 先跑几次状态生成 logs
        client.get("/api/admin/system-status",
                   headers={"Authorization": f"Bearer {token}"})
        # status 不写 logs (那是 persist=False), 这里直接造一条
        # 通过 logs endpoint 验证
        r = client.get("/api/admin/system-health-logs",
                       headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert isinstance(r.json(), list)
    finally:
        app.dependency_overrides.clear()


def test_restart_requires_confirm_string():
    client, token, _ = _client_with_admin_token()
    try:
        r = client.post("/api/admin/restart-api",
                        headers={"Authorization": f"Bearer {token}"},
                        json={"confirm": "wrong"})
        assert r.status_code == 400
        assert "RESTART" in r.json()["detail"]
    finally:
        app.dependency_overrides.clear()


def test_restart_with_correct_confirm_signals_term():
    """正确 confirm 时返回 accepted=true; 实际 SIGTERM 在 0.5s 后, 测试用 patch 拦截."""
    client, token, _ = _client_with_admin_token()
    try:
        from unittest.mock import patch
        with patch("app.services.system_monitor.request_restart") as mock_rr:
            r = client.post("/api/admin/restart-api",
                            headers={"Authorization": f"Bearer {token}"},
                            json={"confirm": "RESTART"})
            assert r.status_code == 200
            assert r.json()["accepted"] is True
            mock_rr.assert_called_once()
    finally:
        app.dependency_overrides.clear()


def test_restart_forbids_non_admin():
    client, _, op_token = _client_with_admin_token()
    try:
        r = client.post("/api/admin/restart-api",
                        headers={"Authorization": f"Bearer {op_token}"},
                        json={"confirm": "RESTART"})
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_system_events_endpoint():
    client, token, _ = _client_with_admin_token()
    try:
        r = client.get("/api/admin/system-events",
                       headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        # 没人写过 → 空列表
        assert body == []
    finally:
        app.dependency_overrides.clear()


def test_system_events_filters_admin_only():
    client, _, op_token = _client_with_admin_token()
    try:
        r = client.get("/api/admin/system-events",
                       headers={"Authorization": f"Bearer {op_token}"})
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_restart_with_db_writes_event():
    """重启 endpoint 应写一条 restart_requested 事件."""
    from unittest.mock import patch
    client, token, _ = _client_with_admin_token()
    try:
        with patch("app.services.system_monitor.request_restart") as mock_rr:
            r = client.post("/api/admin/restart-api",
                            headers={"Authorization": f"Bearer {token}"},
                            json={"confirm": "RESTART"})
        assert r.status_code == 200
        # request_restart 被调时传了 db + actor
        mock_rr.assert_called_once()
        args, kwargs = mock_rr.call_args
        assert kwargs.get("actor") == "admin"
    finally:
        app.dependency_overrides.clear()
