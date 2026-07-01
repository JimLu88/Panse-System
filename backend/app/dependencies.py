"""通用 FastAPI 依赖：当前用户 / 角色守卫。"""
from __future__ import annotations

import hmac
from typing import Optional

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app import page_permissions
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


def enforce_page_permission(
    request: Request,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
) -> None:
    """全局纵深防御 (子账号页面权限): 受限子账号即使绕过前端直接调无权页面的 API 也拦成 403。

    作为 FastAPI 全局依赖挂在所有路由上。用 Depends(get_db) 复用与端点/测试同一个 session
    (尊重测试里的 get_db override; 别自己开 SessionLocal 否则连到另一个空库)。
    session 只是从连接池 checkout, 不查询不产生 DB 往返 → 健康检查等高频请求几乎零开销。
    只有「该路径需要权限」且「携带有效 token」时才真正查一次用户;
    放行路径 / 未带 token / token 无效一律快速返回。admin 与不受限账号在 is_user_allowed 里短路放行。
    未登录时不在此抛 401 — 交给端点自带的 get_current_user 决定 (公开端点仍可访问)。
    """
    path = request.url.path
    if not path.startswith("/api/"):
        return
    perm = page_permissions.perm_for_path(path)
    if perm is None:
        return  # 放行路径 (登录/探针/共享诊断/未命中)
    if not authorization or not authorization.lower().startswith("bearer "):
        return  # 未带 token: 交给端点自带的鉴权决定 401 / 公开
    try:
        payload = auth_service.decode_token(authorization.split(" ", 1)[1].strip())
    except auth_service.InvalidToken:
        return  # token 无效: 同上, 交给端点处理
    user = db.get(User, int(payload.get("uid") or 0))
    if user is None or page_permissions.is_user_allowed(user.role, user.page_perms, path):
        return
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        "子账号未开通此页面权限, 无法访问",
    )
