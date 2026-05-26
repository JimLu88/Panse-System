"""认证服务 (plan §10 Phase 6).

最小化 JWT (HS256) 实现 — 用 hmac + base64 标准库，避免依赖 PyJWT/cryptography。
密码用 bcrypt。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Optional

import bcrypt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.auth import User

settings = get_settings()


# -------- 密码 --------

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# -------- JWT (HS256) --------

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class InvalidToken(Exception):
    pass


def create_token(*, user_id: int, username: str, role: str,
                 ttl_hours: Optional[int] = None,
                 token_type: str = "access") -> str:
    """Phase 13 P3-22: 加 refresh token 机制.

    token_type='access' (默认): TTL 短 (1 小时), 用于 API 调用
    token_type='refresh': TTL 长 (30 天), 仅用于换新 access
    """
    if token_type == "refresh":
        ttl = 30 * 24 * 3600
    else:
        ttl = (ttl_hours or settings.jwt_ttl_hours) * 3600
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": str(user_id),
        "uid": user_id,
        "uname": username,
        "role": role,
        "typ": token_type,
        "iat": now,
        "exp": now + ttl,
    }
    enc_header = _b64url_encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode())
    enc_payload = _b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signing_input = f"{enc_header}.{enc_payload}".encode()
    signature = hmac.new(settings.jwt_secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{enc_header}.{enc_payload}.{_b64url_encode(signature)}"


def create_token_pair(*, user_id: int, username: str, role: str) -> dict:
    """业务: 登录时返回 access + refresh."""
    return {
        "access_token": create_token(user_id=user_id, username=username,
                                      role=role, token_type="access"),
        "refresh_token": create_token(user_id=user_id, username=username,
                                       role=role, token_type="refresh"),
    }


def decode_token(token: str) -> dict[str, Any]:
    try:
        enc_header, enc_payload, enc_sig = token.split(".")
    except ValueError as e:
        raise InvalidToken("malformed token") from e
    signing_input = f"{enc_header}.{enc_payload}".encode()
    expected = hmac.new(settings.jwt_secret.encode(), signing_input, hashlib.sha256).digest()
    actual = _b64url_decode(enc_sig)
    if not hmac.compare_digest(expected, actual):
        raise InvalidToken("bad signature")
    payload = json.loads(_b64url_decode(enc_payload))
    if int(payload.get("exp", 0)) < int(time.time()):
        raise InvalidToken("expired")
    return payload


# -------- User CRUD --------

def create_user(
    db: Session, *, username: str, password: str, role: str = "viewer",
    display_name: Optional[str] = None,
) -> User:
    if db.execute(select(User).where(User.username == username)).scalar_one_or_none():
        raise ValueError(f"user {username!r} already exists")
    from app.models.auth import ROLES
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}; allowed: {ROLES}")
    u = User(
        username=username, display_name=display_name or username,
        password_hash=hash_password(password), role=role,
    )
    db.add(u)
    db.flush()
    return u


def set_password(db: Session, user: User, new_password: str) -> None:
    user.password_hash = hash_password(new_password)
    db.flush()


def update_user(
    db: Session, user: User, *, username: Optional[str] = None,
    display_name: Optional[str] = None, role: Optional[str] = None,
    is_active: Optional[bool] = None,
) -> User:
    from app.models.auth import ROLES
    if username is not None and username != user.username:
        if db.execute(select(User).where(User.username == username)).scalar_one_or_none():
            raise ValueError(f"user {username!r} already exists")
        user.username = username
    if display_name is not None:
        user.display_name = display_name
    if role is not None:
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r}; allowed: {ROLES}")
        user.role = role
    if is_active is not None:
        user.is_active = is_active
    db.flush()
    return user


def authenticate(db: Session, username: str, password: str) -> Optional[User]:
    u = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if u is None or not u.is_active:
        return None
    if not verify_password(password, u.password_hash):
        return None
    return u


def ensure_default_admin(db: Session) -> Optional[User]:
    """如果没有 admin, 创建一个默认 admin/admin (仅 dev/首次启动用)。"""
    existing = db.execute(select(User).where(User.role == "admin")).scalar_one_or_none()
    if existing:
        return None
    u = create_user(db, username="admin", password="admin", role="admin", display_name="默认管理员")
    db.commit()
    return u
