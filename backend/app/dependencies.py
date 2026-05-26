"""通用 FastAPI 依赖：当前用户 / 角色守卫。"""
from __future__ import annotations

import hmac
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.auth import User
from app.services import auth_service, settings_service


def get_current_user_optional(
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """从 Bearer token 解析当前用户；没有就返回 None（不抛）。"""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = auth_service.decode_token(token)
    except auth_service.InvalidToken:
        return None
    return db.get(User, int(payload.get("uid") or 0))


def get_current_user(
    user: Optional[User] = Depends(get_current_user_optional),
) -> User:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "需要登录")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号已停用")
    return user


def require_role(*roles: str):
    """装饰用法：Depends(require_role('admin', 'operator'))."""
    def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"需要角色 {list(roles)}，你的角色是 {user.role!r}",
            )
        return user
    return _dep


def require_ingest_token(
    x_api_key: Optional[str] = Header(None),
    db: Session = Depends(get_db),
) -> bool:
    """机器对机器令牌守卫（供外部采集服务回灌数据，无需用户会话）。

    令牌存 system_settings 的 ``ingest_api_token``（也可用同名环境变量）。
    未配置则拒绝（默认关闭），命中用 constant-time 比较防时序侧信道。
    """
    expected = settings_service.get(db, "ingest_api_token", env_fallback=True)
    if not expected:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "采集令牌未配置")
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "采集令牌无效")
    return True
