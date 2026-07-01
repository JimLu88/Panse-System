from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import page_permissions
from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.models.auth import ROLES, User
from app.rate_limit import limiter
from app.services import auth_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


class LoginOut(BaseModel):
    token: str             # 兼容旧字段, 等同 access_token
    access_token: str
    refresh_token: str
    user: "MeOut"


class MeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    display_name: Optional[str]
    role: str
    is_active: bool
    must_change_password: bool = False
    # 子账号页面权限: None=不受限(全看); list[str]=仅这些页面 permKey 可见。前端据此过滤菜单+守卫路由。
    page_perms: Optional[list[str]] = None


LoginOut.model_rebuild()


@router.post("/login", response_model=LoginOut)
@limiter.limit("10/minute")
def login(request: Request, payload: LoginIn, db: Session = Depends(get_db)):
    try:
        user = auth_service.authenticate(db, payload.username, payload.password)
    except auth_service.LoginLocked as e:
        mins = max(1, (e.remaining + 59) // 60)
        raise HTTPException(429, f"登录失败次数过多, 账号已临时锁定, 请约 {mins} 分钟后再试") from e
    if user is None:
        raise HTTPException(401, "用户名或密码错误")
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    pair = auth_service.create_token_pair(
        user_id=user.id, username=user.username, role=user.role,
    )
    return LoginOut(
        token=pair["access_token"],
        access_token=pair["access_token"],
        refresh_token=pair["refresh_token"],
        user=MeOut.model_validate(user),
    )


class RefreshIn(BaseModel):
    refresh_token: str


class RefreshOut(BaseModel):
    access_token: str


@router.post("/refresh", response_model=RefreshOut)
def refresh(payload: RefreshIn):
    """Phase 13: 用 refresh_token 换新 access_token. refresh 自身过期需重新登录."""
    try:
        data = auth_service.decode_token(payload.refresh_token)
    except auth_service.InvalidToken as e:
        raise HTTPException(401, f"refresh_token 无效: {e}")
    if data.get("typ") != "refresh":
        raise HTTPException(400, "不是 refresh_token")
    new_access = auth_service.create_token(
        user_id=int(data["uid"]), username=data["uname"], role=data["role"],
        token_type="access",
    )
    return RefreshOut(access_token=new_access)


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(get_current_user)):
    return MeOut.model_validate(user)


class UserCreateIn(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=12)
    role: str = Field("viewer")
    display_name: Optional[str] = None
    # 子账号页面权限: None=不受限; list[str]=只能看这些页面 (非法 key 会被后端过滤掉)。admin 恒不受限。
    page_perms: Optional[list[str]] = None


@router.post("/users", response_model=MeOut, status_code=201)
def create_user(
    payload: UserCreateIn,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role("admin")),
):
    try:
        auth_service.validate_password_strength(payload.password, payload.username)
        u = auth_service.create_user(
            db, username=payload.username, password=payload.password,
            role=payload.role, display_name=payload.display_name,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    # admin 恒不受限(None); 其余按传入清洗 (None=不受限, [...]=仅列出页面可见)
    u.page_perms = None if u.role == "admin" else page_permissions.sanitize_perms(payload.page_perms)
    db.commit()
    db.refresh(u)
    return MeOut.model_validate(u)


@router.get("/users", response_model=list[MeOut])
def list_users(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role("admin")),
):
    return [MeOut.model_validate(u) for u in db.execute(select(User).order_by(User.id)).scalars()]


class UserUpdateIn(BaseModel):
    username: Optional[str] = Field(None, min_length=3, max_length=64)
    display_name: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None
    # 子账号页面权限: 只有显式包含该字段才改 (借 model_fields_set 区分「没传」和「传 null」)。
    # 传 [...]=设为仅这些页面; 传 null=恢复不受限; 不传=保持不变。admin 恒被强制不受限。
    page_perms: Optional[list[str]] = None


@router.patch("/users/{user_id}", response_model=MeOut)
def update_user(
    user_id: int,
    payload: UserUpdateIn,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role("admin")),
):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "用户不存在")
    try:
        auth_service.update_user(
            db, u, username=payload.username, display_name=payload.display_name,
            role=payload.role, is_active=payload.is_active,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    # page_perms: 仅在请求显式带了该字段时更新 (None=不受限 / list=受限)
    if "page_perms" in payload.model_fields_set:
        u.page_perms = page_permissions.sanitize_perms(payload.page_perms)
    # admin 恒不受限 — 即使改成 admin 或本就是 admin, 都清空 page_perms
    if u.role == "admin":
        u.page_perms = None
    db.commit()
    db.refresh(u)
    return MeOut.model_validate(u)


class PasswordResetIn(BaseModel):
    new_password: str = Field(..., min_length=12)


@router.post("/users/{user_id}/password", status_code=204)
def reset_password(
    user_id: int,
    payload: PasswordResetIn,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role("admin")),
):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "用户不存在")
    try:
        auth_service.validate_password_strength(payload.new_password, u.username)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    auth_service.set_password(db, u, payload.new_password)
    db.commit()


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str = Field(..., min_length=12)


@router.post("/me/password", status_code=204)
def change_my_password(
    payload: ChangePasswordIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not auth_service.verify_password(payload.old_password, user.password_hash):
        raise HTTPException(400, "原密码错误")
    try:
        auth_service.validate_password_strength(payload.new_password, user.username)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    auth_service.set_password(db, user, payload.new_password)
    db.commit()


class RolesOut(BaseModel):
    roles: list[str]
    descriptions: dict[str, str]


@router.get("/roles", response_model=RolesOut)
def get_roles():
    from app.models.auth import ROLE_DESC
    return RolesOut(roles=list(ROLES), descriptions=ROLE_DESC)
