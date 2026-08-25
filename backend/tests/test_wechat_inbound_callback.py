import base64
import time

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models import Base
from app.models.settings import SystemSetting
from app.services import settings_service, wechat_inbound_service


AES_KEY = base64.b64encode(bytes(range(32))).decode("ascii").rstrip("=")
CORP_ID = "ww-test-corp"
TOKEN = "callback-test-token"
ALLOWED_USER = "OwnerUserId"


def _client_and_session():
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = factory()

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app), session


def _configure(db, *, allowed_user=ALLOWED_USER):
    settings_service.set_value(db, wechat_inbound_service.KEY_ENABLED, "true")
    settings_service.set_value(db, wechat_inbound_service.KEY_CORP_ID, CORP_ID)
    settings_service.set_value(db, wechat_inbound_service.KEY_TOKEN, TOKEN)
    settings_service.set_value(db, wechat_inbound_service.KEY_AES_KEY, AES_KEY)
    settings_service.set_value(db, wechat_inbound_service.KEY_ALLOWED_USERS, allowed_user)
    db.commit()


def _callback_payload(content: str, *, sender=ALLOWED_USER, message_id="90001"):
    inner = (
        "<xml>"
        f"<ToUserName>{CORP_ID}</ToUserName>"
        f"<FromUserName>{sender}</FromUserName>"
        "<CreateTime>1710000000</CreateTime>"
        "<MsgType>text</MsgType>"
        f"<Content>{content}</Content>"
        f"<MsgId>{message_id}</MsgId>"
        "<AgentID>1000002</AgentID>"
        "</xml>"
    )
    encrypted = wechat_inbound_service.encrypt_message(inner, AES_KEY, CORP_ID)
    timestamp = str(int(time.time()))
    nonce = "callback-nonce"
    sig = wechat_inbound_service.signature(TOKEN, timestamp, nonce, encrypted)
    outer = f"<xml><Encrypt><![CDATA[{encrypted}]]></Encrypt></xml>".encode()
    return outer, {"msg_signature": sig, "timestamp": timestamp, "nonce": nonce}


def test_url_verification_decrypts_echo_string():
    client, db = _client_and_session()
    try:
        _configure(db)
        echo = wechat_inbound_service.encrypt_message("verified-echo", AES_KEY, CORP_ID)
        timestamp = str(int(time.time()))
        nonce = "verify-nonce"
        sig = wechat_inbound_service.signature(TOKEN, timestamp, nonce, echo)
        response = client.get("/api/wechat/callback", params={
            "msg_signature": sig,
            "timestamp": timestamp,
            "nonce": nonce,
            "echostr": echo,
        })
        assert response.status_code == 200
        assert response.text == "verified-echo"
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_valid_allowed_password_dispatches_once_without_echo(monkeypatch):
    client, db = _client_and_session()
    captured = []
    monkeypatch.setattr(
        wechat_inbound_service,
        "dispatch",
        lambda command: captured.append(command),
    )
    try:
        _configure(db)
        body, params = _callback_payload("发货密码：Secret-123", message_id="90002")
        first = client.post("/api/wechat/callback", params=params, content=body)
        second = client.post("/api/wechat/callback", params=params, content=body)
        assert first.status_code == 200
        assert first.text == "success"
        assert second.status_code == 200
        assert len(captured) == 1
        assert captured[0].password == "Secret-123"
        assert "Secret-123" not in first.text
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_callback_rejects_bad_signature_and_unauthorized_sender(monkeypatch):
    client, db = _client_and_session()
    captured = []
    monkeypatch.setattr(wechat_inbound_service, "dispatch", captured.append)
    try:
        _configure(db)
        body, params = _callback_payload("发货密码：Secret-123")
        bad = client.post(
            "/api/wechat/callback",
            params={**params, "msg_signature": "bad"},
            content=body,
        )
        assert bad.status_code == 403

        body, params = _callback_payload(
            "发货密码：Secret-123", sender="NotAllowed", message_id="90003",
        )
        forbidden = client.post("/api/wechat/callback", params=params, content=body)
        assert forbidden.status_code == 403
        assert not captured
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_only_explicit_shipping_password_command_is_accepted(monkeypatch):
    client, db = _client_and_session()
    captured = []
    monkeypatch.setattr(wechat_inbound_service, "dispatch", captured.append)
    try:
        _configure(db)
        for index, content in enumerate(("密码：Secret-123", "Secret-123", "发货密码Secret-123")):
            body, params = _callback_payload(content, message_id=f"91{index}")
            response = client.post("/api/wechat/callback", params=params, content=body)
            assert response.status_code == 200
        assert not captured
    finally:
        app.dependency_overrides.clear()
        db.close()


def test_future_shipping_password_is_encrypted_at_rest(db_session):
    settings_service.set_value(db_session, "taobao_shipping_pwd_latest", "Sensitive-Password")
    db_session.commit()
    row = db_session.execute(
        select(SystemSetting).where(SystemSetting.key == "taobao_shipping_pwd_latest")
    ).scalar_one()
    assert row.is_secret is True
    assert row.value_plain is None
    assert "Sensitive-Password" not in (row.value_encrypted or "")
    assert settings_service.get(
        db_session, "taobao_shipping_pwd_latest", env_fallback=False,
    ) == "Sensitive-Password"
