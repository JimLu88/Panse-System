"""通知服务: webhook payload 构造 + 静默关闭 + 集成测试."""
from __future__ import annotations

import base64
import hashlib
from unittest.mock import patch

import pytest

from app.services import notify_service, settings_service


@pytest.fixture(autouse=True)
def _allow_notify_in_this_module(monkeypatch):
    """conftest 全局设了 PANSE_DISABLE_NOTIFY (防测试轰炸真实飞书群);
    本文件专测 notify 本身且全程 mock HTTP, 安全豁免。"""
    monkeypatch.delenv("PANSE_DISABLE_NOTIFY", raising=False)


@pytest.fixture(autouse=True)
def _wechat_scope_all(db_session):
    """多数用例测 notify【发送机制】本身 → 放开企微门(scope=all);
    企微门的默认拦截由 test_wechat_gate_* 在体内改回 briefing_only 单独验(2026-07-06)。"""
    settings_service.set_value(db_session, "wechat_push_scope", "all")
    db_session.commit()


def test_notify_returns_false_when_no_config(db_session):
    """无 provider 配置 → 静默返回 (False, '未配置 ...')."""
    ok, detail = notify_service.notify(db_session, "test")
    assert ok is False
    assert "未配置" in detail


def test_notify_returns_false_when_provider_none(db_session):
    settings_service.set_value(db_session, "notify_provider", "none")
    settings_service.set_value(db_session, "notify_webhook", "https://x.example")
    db_session.commit()
    ok, _ = notify_service.notify(db_session, "test")
    assert ok is False


def test_notify_returns_false_when_webhook_missing(db_session):
    settings_service.set_value(db_session, "notify_provider", "slack")
    db_session.commit()
    ok, _ = notify_service.notify(db_session, "test")
    assert ok is False


def test_notify_slack_payload(db_session):
    settings_service.set_value(db_session, "notify_provider", "slack")
    settings_service.set_value(db_session, "notify_webhook", "https://hooks.slack.com/x")
    db_session.commit()

    captured = {}
    def fake_post(url, body, *, timeout_sec=5):
        captured["url"] = url
        captured["body"] = body
        return True, "ok"

    with patch("app.services.notify_service._post_json", side_effect=fake_post):
        ok, _ = notify_service.notify(db_session, "看门狗触发", level="error",
                                       title="告警")
    assert ok is True
    assert captured["url"] == "https://hooks.slack.com/x"
    assert "text" in captured["body"]
    assert "看门狗触发" in captured["body"]["text"]
    assert "🚨" in captured["body"]["text"]


def test_notify_wechat_payload(db_session):
    settings_service.set_value(db_session, "notify_provider", "wechat_work")
    settings_service.set_value(db_session, "notify_webhook", "https://qyapi/x")
    db_session.commit()

    captured = {}
    def fake_post(url, body, *, timeout_sec=5):
        captured["body"] = body
        return True, "ok"
    with patch("app.services.notify_service._post_json", side_effect=fake_post):
        notify_service.notify(db_session, "test", level="info")
    assert captured["body"]["msgtype"] == "text"
    assert "content" in captured["body"]["text"]


def test_notify_wechat_image_payload(db_session):
    settings_service.set_value(db_session, "notify_provider", "wechat_work")
    settings_service.set_value(db_session, "notify_webhook", "https://qyapi/x")
    db_session.commit()
    captured = []

    with patch(
        "app.services.notify_service._post_json",
        side_effect=lambda url, body, **kwargs: captured.append(body) or (True, "ok"),
    ):
        result = notify_service.notify_image(
            db_session,
            b"qr-png",
            text="请扫码",
            title="取数登录",
            wechat_allowed=True,
        )

    assert result["text"] is True
    assert result["image"] is True
    assert [item["msgtype"] for item in captured] == ["text", "image"]
    assert captured[1]["image"] == {
        "base64": base64.b64encode(b"qr-png").decode("ascii"),
        "md5": hashlib.md5(b"qr-png").hexdigest(),  # noqa: S324 - protocol assertion
    }


def test_notify_dingtalk_payload(db_session):
    settings_service.set_value(db_session, "notify_provider", "dingtalk")
    settings_service.set_value(db_session, "notify_webhook", "https://oapi.dingtalk.com/x")
    db_session.commit()
    captured = {}
    def fake_post(url, body, *, timeout_sec=5):
        captured["body"] = body
        return True, "ok"
    with patch("app.services.notify_service._post_json", side_effect=fake_post):
        notify_service.notify(db_session, "msg")
    assert captured["body"]["msgtype"] == "text"
    assert captured["body"]["text"]["content"]


def test_notify_feishu_payload(db_session):
    settings_service.set_value(db_session, "notify_provider", "feishu")
    settings_service.set_value(db_session, "notify_webhook", "https://feishu/x")
    db_session.commit()
    captured = {}
    def fake_post(url, body, *, timeout_sec=5):
        captured["body"] = body
        return True, "ok"
    with patch("app.services.notify_service._post_json", side_effect=fake_post):
        notify_service.notify(db_session, "msg")
    assert captured["body"]["msg_type"] == "text"
    assert captured["body"]["content"]["text"]


def test_notify_swallows_http_error(db_session):
    settings_service.set_value(db_session, "notify_provider", "slack")
    settings_service.set_value(db_session, "notify_webhook", "https://hooks.slack.com/x")
    db_session.commit()
    # 让 _post_json 返回失败 (模拟 404)
    with patch("app.services.notify_service._post_json",
               return_value=(False, "HTTP 404: Not Found")):
        ok, detail = notify_service.notify(db_session, "test")
    assert ok is False
    assert "404" in detail


def _cfg_wechat(db_session):
    settings_service.set_value(db_session, "notify_provider", "wechat_work")
    settings_service.set_value(db_session, "notify_webhook", "https://qyapi/x")
    db_session.commit()


def test_wechat_gate_blocks_noise_by_default(db_session):
    """默认 briefing_only: 未标 wechat_allowed 的调用不推企微(静默, 不发请求)。"""
    _cfg_wechat(db_session)
    settings_service.set_value(db_session, "wechat_push_scope", "briefing_only")  # 覆盖 autouse
    db_session.commit()
    with patch("app.services.notify_service._post_json", return_value=(True, "ok")) as m:
        ok, detail = notify_service.notify(db_session, "噪音推送")
    assert ok is False and "仅经营日报" in detail
    m.assert_not_called()


def test_wechat_gate_allows_briefing(db_session):
    """标 wechat_allowed=True(经营日报/测试) → 放行到企微。"""
    _cfg_wechat(db_session)
    settings_service.set_value(db_session, "wechat_push_scope", "briefing_only")
    db_session.commit()
    with patch("app.services.notify_service._post_json", return_value=(True, "ok")) as m:
        ok, _ = notify_service.notify(db_session, "经营日报", wechat_allowed=True)
    assert ok is True
    m.assert_called_once()


def test_wechat_gate_scope_all_restores_everything(db_session):
    """设 wechat_push_scope=all → 全部放行(恢复旧行为)。"""
    _cfg_wechat(db_session)  # autouse 已设 scope=all
    with patch("app.services.notify_service._post_json", return_value=(True, "ok")) as m:
        ok, _ = notify_service.notify(db_session, "随便一条")
    assert ok is True
    m.assert_called_once()


def test_get_config_masks_webhook(db_session):
    settings_service.set_value(db_session, "notify_provider", "slack")
    settings_service.set_value(db_session, "notify_webhook", "https://hooks.slack.com/T0000/AAA/BBB")
    db_session.commit()
    cfg = notify_service.get_config(db_session)
    # get_config 现在不脱敏 (admin API 自己脱敏); 但确认有取到值
    assert cfg["provider"] == "slack"
    assert cfg["webhook"].startswith("https://hooks.slack.com/")
    assert cfg["webhook_set"] is True


# ----------------------------- admin API endpoints ------------------ #


def _api_client_with_admin():
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.database import get_db
    from app.main import app
    from app.models import Base
    from app.services import auth_service

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
    return TestClient(app), token


def test_notify_config_endpoint_get_put_test():
    client, token = _api_client_with_admin()
    h = {"Authorization": f"Bearer {token}"}
    try:
        # 初始: provider=none, 未设
        r = client.get("/api/admin/notify-config", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["provider"] == "none"
        assert body["webhook_set"] is False
        assert any(p["value"] == "slack" for p in body["supported_providers"])

        # PUT 设置
        r = client.put("/api/admin/notify-config", headers=h, json={
            "provider": "slack",
            "webhook": "https://hooks.slack.com/services/T/B/secret",
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["provider"] == "slack"
        assert body["webhook_set"] is True
        # 脱敏 — 不应包含完整 token
        assert "secret" not in body["webhook_masked"]

        # 清除
        r = client.put("/api/admin/notify-config", headers=h,
                       json={"webhook": "__CLEAR__"})
        assert r.status_code == 200
        assert r.json()["webhook_set"] is False

        # test 接口 (无 webhook → 返回 ok=False)
        r = client.post("/api/admin/notify-config/test", headers=h)
        assert r.status_code == 200
        assert r.json()["ok"] is False
    finally:
        from app.main import app
        app.dependency_overrides.clear()


def test_wechat_inbound_admin_config_never_returns_secrets():
    import base64

    client, token = _api_client_with_admin()
    h = {"Authorization": f"Bearer {token}"}
    aes_key = base64.b64encode(bytes(range(32))).decode("ascii").rstrip("=")
    try:
        incomplete = client.put("/api/admin/wechat-inbound-config", headers=h, json={
            "enabled": True,
            "corp_id": "ww-test-corp",
        })
        assert incomplete.status_code == 400

        response = client.put("/api/admin/wechat-inbound-config", headers=h, json={
            "enabled": True,
            "corp_id": "ww-test-corp",
            "token": "callback-secret-token",
            "aes_key": aes_key,
            "allowed_users": ["OwnerUserId"],
        })
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["enabled"] is True
        assert body["ready"] is True
        assert body["token_set"] is True
        assert body["aes_key_set"] is True
        assert body["allowed_users"] == ["OwnerUserId"]
        assert "callback-secret-token" not in response.text
        assert aes_key not in response.text
    finally:
        from app.main import app
        app.dependency_overrides.clear()
