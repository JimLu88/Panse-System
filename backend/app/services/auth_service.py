"""认证服务 (plan §10 Phase 6).

最小化 JWT (HS256) 实现 — 用 hmac + base64 标准库，避免依赖 PyJWT/cryptography。
密码用 bcrypt。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from typing import Any, Optional

import bcrypt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.auth import User

settings = get_settings()
_logger = logging.getLogger("panse.auth")

_DEFAULT_JWT_SECRET = "panse-dev-secret-CHANGE-ME-in-production"
_jwt_secret_cache: Optional[str] = None


def _jwt_secret() -> str:
    """token 签名密钥: JWT_SECRET 环境变量(非默认值)优先; 否则生成并持久化一个随机密钥。

    避免用公开仓库里的默认密钥签发可被任何人伪造的 token (外网 DDNS 暴露下尤其危险)。
    与 settings_service 的加密密钥解耦: 改这个不会影响已加密存储的 AI key。
    """
    global _jwt_secret_cache
    if _jwt_secret_cache:
        return _jwt_secret_cache
    env = (os.environ.get("JWT_SECRET") or "").strip()
    if env and env != _DEFAULT_JWT_SECRET:
        _jwt_secret_cache = env
        return env
    path = os.environ.get("JWT_SECRET_FILE", "/app/storage/.jwt_secret")
    try:
        if os.path.isfile(path):
            val = open(path, encoding="utf-8").read().strip()
            if val:
                _jwt_secret_cache = val
                return val
        os.makedirs(os.path.dirname(path), exist_ok=True)
        val = secrets.token_urlsafe(48)
        with open(path, "w", encoding="utf-8") as f:
            f.write(val)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        _logger.warning("未配置 JWT_SECRET, 已生成随机密钥持久化到 %s "
                        "(token 不再用公开默认密钥签发; 现有登录态需重新登录一次)", path)
        _jwt_secret_cache = val
        return val
    except OSError as e:
        _logger.error("JWT 密钥落盘失败 (%s), 退化为进程内随机密钥 (重启需重新登录)", e)
        _jwt_secret_cache = secrets.token_urlsafe(48)
        return _jwt_secret_cache


# -------- 密码 --------

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# 常见弱密码黑名单 (小写比对) — 公网暴露下挡掉撞库首选目标
_WEAK_PASSWORDS = frozenset({
    "password", "passw0rd", "password123", "12345678", "123456789", "1234567890",
    "qwertyuiop", "qwerty123", "admin12345", "administrator", "panse123456",
    "111111111111", "000000000000", "abcd1234abcd", "iloveyou123", "8888888888",
    "a123456789b", "1q2w3e4r5t", "1qaz2wsx3edc", "woaini1314520", "123123123123",
})


def validate_password_strength(password: str, username: Optional[str] = None) -> None:
    """密码强度校验 (公网暴露后加固; 仅在创建/改密的 API 层调用)。不合格抛 ValueError。

    只约束"新设的密码"; 现有账号的旧密码不受影响, 不强制改密。
    """
    if len(password) < 8:
        raise ValueError("密码至少 8 位")
    low = password.lower()
    if low in _WEAK_PASSWORDS:
        raise ValueError("密码过于常见, 容易被撞库, 请换一个")
    if username and len(username) >= 3 and username.lower() in low:
        raise ValueError("密码不能包含用户名")
    if len(set(password)) <= 3:
        raise ValueError("密码字符过于单一")


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
    signature = hmac.new(_jwt_secret().encode(), signing_input, hashlib.sha256).digest()
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
    expected = hmac.new(_jwt_secret().encode(), signing_input, hashlib.sha256).digest()
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
    user.must_change_password = False   # 改完密码即清除强制改密标记
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


# -------- 登录失败锁定 (内存; api 单进程, 无需建表) --------
# 公网暴露后防在线爆破: 同一用户名在窗口内连续失败到阈值 → 临时锁定。
# 按"账号"锁(与 IP 无关) → 攻击者换 IP / 伪造 X-Forwarded-For 也绕不过。
_LOCK_THRESHOLD = 10     # 窗口内连续失败次数上限 (正常用户极少触发)
_LOCK_WINDOW = 900       # 失败计数窗口 (秒); 超过无新失败则计数清零
_LOCK_DURATION = 900     # 锁定时长 (秒) = 15 分钟
_login_fail_state: dict[str, dict[str, float]] = {}


class LoginLocked(Exception):
    """账号因连续登录失败被临时锁定。remaining = 剩余锁定秒数。"""
    def __init__(self, remaining: int):
        self.remaining = remaining
        super().__init__(f"locked for {remaining}s")


def _lock_remaining(username: str) -> int:
    st = _login_fail_state.get(username.lower())
    if not st:
        return 0
    rem = st.get("locked_until", 0.0) - time.time()
    return int(rem) if rem > 0 else 0


def _record_login_fail(username: str) -> None:
    now = time.time()
    key = username.lower()
    st = _login_fail_state.get(key) or {"fails": 0.0, "last_fail": 0.0, "locked_until": 0.0}
    if now - st["last_fail"] > _LOCK_WINDOW:
        st["fails"] = 0.0
    st["fails"] += 1
    st["last_fail"] = now
    if st["fails"] >= _LOCK_THRESHOLD:
        st["locked_until"] = now + _LOCK_DURATION
        st["fails"] = 0.0
    _login_fail_state[key] = st
    if len(_login_fail_state) > 1000:   # 防内存膨胀: 清过期条目
        for k in [k for k, v in _login_fail_state.items()
                  if v.get("locked_until", 0.0) < now and now - v.get("last_fail", 0.0) > _LOCK_WINDOW]:
            _login_fail_state.pop(k, None)


def reset_login_lockstate() -> None:
    """测试/管理用: 清空登录失败计数与锁定。"""
    _login_fail_state.clear()


def authenticate(db: Session, username: str, password: str) -> Optional[User]:
    remaining = _lock_remaining(username)
    if remaining > 0:
        raise LoginLocked(remaining)
    u = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
    if u is None or not u.is_active:
        return None
    if not verify_password(password, u.password_hash):
        _record_login_fail(username)   # 仅对真实活跃账号计数 (不存在的用户名不记, 防内存膨胀)
        return None
    _login_fail_state.pop(username.lower(), None)   # 成功 → 清零
    return u


def ensure_default_admin(db: Session) -> Optional[User]:
    """如果没有 admin, 创建一个默认 admin/admin (仅 dev/首次启动用)。"""
    existing = db.execute(select(User).where(User.role == "admin")).scalar_one_or_none()
    if existing:
        return None
    u = create_user(db, username="admin", password="admin", role="admin", display_name="默认管理员")
    u.must_change_password = True   # 默认 admin/admin 必须首次登录改密
    db.commit()
    return u


def flag_weak_default_passwords(db: Session) -> int:
    """启动时主动保护: 把仍在用弱默认密码 'admin' 的活跃账号标记为必须改密。
    只命中密码恰为 'admin' 的账号; 已改过密码的不受影响。返回标记数。"""
    n = 0
    try:
        # 仅查活跃的 admin 账号: 默认弱密码就是 admin/admin (admin 角色), 同时避免对全部用户跑 bcrypt
        users = db.execute(
            select(User).where(User.is_active.is_(True), User.role == "admin")
        ).scalars().all()
        for u in users:
            if not u.must_change_password and verify_password("admin", u.password_hash):
                u.must_change_password = True
                n += 1
        if n:
            db.commit()
    except Exception:  # pragma: no cover
        db.rollback()
    return n
