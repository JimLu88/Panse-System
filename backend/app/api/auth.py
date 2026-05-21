from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.models.auth import ROLES, User
from app.services import auth_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


class LoginOut(BaseModel):
    token: str
    user: "MeOut"


class MeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    display_name: Optional[str]
    role: str
    is_active: bool


LoginOut.model_rebuild()


@router.post("/login", response_model=LoginOut)
def login(payload: LoginIn, db: Session = Depends(get_db)):
    user = auth_service.authenticate(db, payload.username, payload.password)
    if user is None:
        raise HTTPException(401, "用户名或密码错误")
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    token = auth_service.create_token(user_id=user.id, username=user.username, role=user.role)
    return LoginOut(token=token, user=MeOut.model_validate(user))


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(get_current_user)):
    return MeOut.model_validate(user)


class UserCreateIn(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=6)
    role: str = Field("viewer")
    display_name: Optional[str] = None


@router.post("/users", response_model=MeOut, status_code=201)
def create_user(
    payload: UserCreateIn,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role("admin")),
):
    try:
        u = auth_service.create_user(
            db, username=payload.username, password=payload.password,
            role=payload.role, display_name=payload.display_name,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    db.commit()
    db.refresh(u)
    return MeOut.model_validate(u)


@router.get("/users", response_model=list[MeOut])
def list_users(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_role("admin")),
):
    return [MeOut.model_validate(u) for u in db.execute(select(User).order_by(User.id)).scalars()]


class RolesOut(BaseModel):
    roles: list[str]
    descriptions: dict[str, str]


@router.get("/roles", response_model=RolesOut)
def get_roles():
    from app.models.auth import ROLE_DESC
    return RolesOut(roles=list(ROLES), descriptions=ROLE_DESC)
