"""通用 FastAPI 依赖：当前用户 / 角色守卫。"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.auth import User
from app.services import auth_service


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
