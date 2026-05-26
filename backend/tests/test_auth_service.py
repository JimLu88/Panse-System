import time

import pytest

from app.models.auth import User
from app.services import auth_service


# -------- 密码 --------

def test_hash_and_verify_password():
    h = auth_service.hash_password("hunter2")
    assert auth_service.verify_password("hunter2", h)
    assert not auth_service.verify_password("wrong", h)


def test_verify_invalid_hash():
    assert not auth_service.verify_password("x", "not-a-bcrypt-hash")


# -------- JWT --------

def test_create_and_decode_token():
    t = auth_service.create_token(user_id=1, username="alice", role="admin")
    payload = auth_service.decode_token(t)
    assert payload["uid"] == 1
    assert payload["uname"] == "alice"
    assert payload["role"] == "admin"
    assert payload["exp"] > int(time.time())


def test_decode_rejects_tampered_payload():
    t = auth_service.create_token(user_id=1, username="alice", role="viewer")
    # 改一个字符破坏签名
    parts = t.split(".")
    bad = ".".join([parts[0], parts[1][:-1] + ("X" if parts[1][-1] != "X" else "Y"), parts[2]])
    with pytest.raises(auth_service.InvalidToken):
        auth_service.decode_token(bad)


def test_decode_rejects_expired(monkeypatch):
    t = auth_service.create_token(user_id=1, username="x", role="viewer", ttl_hours=1)
    # 模拟时间过去 2 小时
    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 7200)
    with pytest.raises(auth_service.InvalidToken):
        auth_service.decode_token(t)


def test_decode_rejects_malformed():
    with pytest.raises(auth_service.InvalidToken):
        auth_service.decode_token("not.a.token.format.x")
    with pytest.raises(auth_service.InvalidToken):
        auth_service.decode_token("nodots")


# -------- User CRUD --------

def test_create_user_and_authenticate(db_session):
    u = auth_service.create_user(db_session, username="alice", password="pw1234", role="admin")
    db_session.commit()
    assert u.role == "admin"
    assert auth_service.authenticate(db_session, "alice", "pw1234") is not None
    assert auth_service.authenticate(db_session, "alice", "wrong") is None
    assert auth_service.authenticate(db_session, "noone", "x") is None


def test_create_user_rejects_duplicate(db_session):
    auth_service.create_user(db_session, username="alice", password="pw1234")
    db_session.commit()
    with pytest.raises(ValueError):
        auth_service.create_user(db_session, username="alice", password="other")


def test_create_user_rejects_bad_role(db_session):
    with pytest.raises(ValueError):
        auth_service.create_user(db_session, username="x", password="pw1234", role="superhero")


def test_inactive_user_cannot_login(db_session):
    u = auth_service.create_user(db_session, username="bob", password="pw1234")
    u.is_active = False
    db_session.commit()
    assert auth_service.authenticate(db_session, "bob", "pw1234") is None


def test_ensure_default_admin_creates_once(db_session):
    u = auth_service.ensure_default_admin(db_session)
    assert u is not None
    assert u.username == "admin"
    # 第二次不再创建
    u2 = auth_service.ensure_default_admin(db_session)
    assert u2 is None
    assert db_session.query(User).filter_by(role="admin").count() == 1
