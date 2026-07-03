"""全局认证网关 require_authenticated (优化 #1, 2026-07-04)。

影子模式 (默认): 匿名访问敏感端点只记不拦 → 不破坏现有流量。
强制模式 (PANSE_AUTH_ENFORCE=1): 匿名 401; 有效 Bearer / 机器 X-API-Key / 公开路径放行。
"""
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import dependencies as deps
from app.services import auth_service, settings_service


def _req(path: str, method: str = "GET", client: str = "10.0.0.9") -> Request:
    return Request({
        "type": "http", "method": method, "path": path,
        "headers": [], "client": (client, 0), "query_string": b"",
    })


# ---------------- 公开路径判定 ----------------

def test_public_paths_recognized():
    assert deps._is_public_path("/api/health") is True
    assert deps._is_public_path("/api/ready") is True
    assert deps._is_public_path("/api/version") is True
    assert deps._is_public_path("/api/auth/login") is True
    assert deps._is_public_path("/api/auth/refresh") is True
    assert deps._is_public_path("/api/alerts/stream") is True
    assert deps._is_public_path("/api/alerts/stream/whatever") is True


def test_sensitive_paths_not_public():
    assert deps._is_public_path("/api/orders") is False
    assert deps._is_public_path("/api/finance/alipay-flows") is False
    assert deps._is_public_path("/api/auth/me") is False       # me 需登录, 不在放行名单


# ---------------- 影子模式: 只记不拦 ----------------

def test_shadow_mode_allows_anonymous(monkeypatch, db_session):
    monkeypatch.delenv("PANSE_AUTH_ENFORCE", raising=False)
    deps._shadow_seen.clear()
    # 匿名访问敏感端点 — 影子模式必须放行 (返回 None 不抛)
    assert deps.require_authenticated(_req("/api/orders"), authorization=None,
                                      x_api_key=None, db=db_session) is None
    # 记了一条 would-block
    assert ("GET", "/api/orders") in deps._shadow_seen


def test_shadow_mode_dedups(monkeypatch, db_session):
    monkeypatch.delenv("PANSE_AUTH_ENFORCE", raising=False)
    deps._shadow_seen.clear()
    for _ in range(5):
        deps.require_authenticated(_req("/api/finance/x"), authorization=None,
                                   x_api_key=None, db=db_session)
    # 同 method+path 只记一次
    assert sum(1 for s in deps._shadow_seen if s == ("GET", "/api/finance/x")) == 1


# ---------------- 强制模式: 匿名 401 ----------------

def test_enforce_blocks_anonymous(monkeypatch, db_session):
    monkeypatch.setenv("PANSE_AUTH_ENFORCE", "1")
    with pytest.raises(HTTPException) as ei:
        deps.require_authenticated(_req("/api/orders"), authorization=None,
                                   x_api_key=None, db=db_session)
    assert ei.value.status_code == 401


def test_enforce_allows_public(monkeypatch, db_session):
    monkeypatch.setenv("PANSE_AUTH_ENFORCE", "1")
    for p in ("/api/health", "/api/auth/login", "/api/version"):
        assert deps.require_authenticated(_req(p), authorization=None,
                                          x_api_key=None, db=db_session) is None


def test_enforce_allows_valid_bearer(monkeypatch, db_session):
    monkeypatch.setenv("PANSE_AUTH_ENFORCE", "1")
    monkeypatch.setattr(auth_service, "decode_token", lambda t: {"uid": 1, "uname": "boss"})
    assert deps.require_authenticated(_req("/api/orders"), authorization="Bearer good.token",
                                      x_api_key=None, db=db_session) is None


def test_enforce_rejects_invalid_bearer(monkeypatch, db_session):
    monkeypatch.setenv("PANSE_AUTH_ENFORCE", "1")
    def _boom(_t):
        raise auth_service.InvalidToken("bad")
    monkeypatch.setattr(auth_service, "decode_token", _boom)
    with pytest.raises(HTTPException) as ei:
        deps.require_authenticated(_req("/api/orders"), authorization="Bearer bad.token",
                                   x_api_key=None, db=db_session)
    assert ei.value.status_code == 401


def test_enforce_allows_valid_machine_key(monkeypatch, db_session):
    monkeypatch.setenv("PANSE_AUTH_ENFORCE", "1")
    settings_service.set_value(db_session, "ingest_api_token", "S3CRET-INGEST-KEY")
    db_session.commit()
    assert deps.require_authenticated(_req("/api/web-agent/ingest", method="POST"),
                                      authorization=None, x_api_key="S3CRET-INGEST-KEY",
                                      db=db_session) is None


def test_enforce_rejects_wrong_machine_key(monkeypatch, db_session):
    monkeypatch.setenv("PANSE_AUTH_ENFORCE", "1")
    settings_service.set_value(db_session, "ingest_api_token", "S3CRET-INGEST-KEY")
    db_session.commit()
    with pytest.raises(HTTPException) as ei:
        deps.require_authenticated(_req("/api/web-agent/ingest", method="POST"),
                                   authorization=None, x_api_key="WRONG-KEY", db=db_session)
    assert ei.value.status_code == 401
