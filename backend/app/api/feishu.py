from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.models.auth import User
from app.models.exception import DataException
from app.models.feishu_sync import FeishuTableBinding
from app.services import feishu_client, feishu_sync_service, settings_service

router = APIRouter(prefix="/api/feishu", tags=["feishu"])


class BindingIn(BaseModel):
    system_table: str
    feishu_app_token: str
    feishu_table_id: str
    direction: str = "bidirectional"
    field_mapping: Optional[str] = None
    enabled: bool = False


class BindingUpdateIn(BaseModel):
    feishu_app_token: Optional[str] = None
    feishu_table_id: Optional[str] = None
    direction: Optional[str] = None
    field_mapping: Optional[str] = None
    enabled: Optional[bool] = None


class BindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    system_table: str
    feishu_app_token: str
    feishu_table_id: str
    direction: str
    enabled: bool
    field_mapping: Optional[str]


class StatusOut(BaseModel):
    system_table: str
    feishu_table_id: str
    direction: str
    enabled: bool
    mapped_rows: int


@router.get("/bindings", response_model=list[BindingOut])
def list_bindings(db: Session = Depends(get_db)):
    return db.execute(select(FeishuTableBinding).order_by(FeishuTableBinding.system_table)).scalars().all()


@router.post("/bindings", response_model=BindingOut, status_code=201)
def create_binding(payload: BindingIn, db: Session = Depends(get_db),
                   _: User = Depends(require_role("admin"))):
    existing = db.execute(
        select(FeishuTableBinding).where(FeishuTableBinding.system_table == payload.system_table)
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(409, f"binding for system_table {payload.system_table} already exists")
    b = FeishuTableBinding(**payload.model_dump())
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


@router.patch("/bindings/{binding_id}", response_model=BindingOut)
def update_binding(binding_id: int, payload: BindingUpdateIn, db: Session = Depends(get_db),
                   _: User = Depends(require_role("admin"))):
    b = db.get(FeishuTableBinding, binding_id)
    if b is None:
        raise HTTPException(404, "binding 不存在")
    for f, v in payload.model_dump(exclude_unset=True).items():
        setattr(b, f, v)
    db.commit()
    db.refresh(b)
    return b


@router.delete("/bindings/{binding_id}", status_code=204)
def delete_binding(binding_id: int, db: Session = Depends(get_db),
                   _: User = Depends(require_role("admin"))):
    b = db.get(FeishuTableBinding, binding_id)
    if b is not None:
        db.delete(b)
        db.commit()


@router.get("/status", response_model=list[StatusOut])
def get_status(db: Session = Depends(get_db)):
    return [StatusOut(**s.__dict__) for s in feishu_sync_service.list_status(db)]


@router.get("/supported-tables")
def supported_tables():
    return {"tables": feishu_sync_service.SUPPORTED_TABLES}


# ----------------------------- 凭证 ----------------------------- #


class CredentialsIn(BaseModel):
    app_id: Optional[str] = None
    app_secret: Optional[str] = None   # "__CLEAR__" 清除


class CredentialsOut(BaseModel):
    app_id: str
    app_secret_masked: str
    configured: bool


@router.get("/credentials", response_model=CredentialsOut)
def get_credentials(db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    app_id = settings_service.get(db, "feishu_app_id", env_fallback=False) or ""
    secret = settings_service.get(db, "feishu_app_secret", env_fallback=False) or ""
    return CredentialsOut(
        app_id=app_id,
        app_secret_masked=settings_service.mask_secret(secret),
        configured=bool(app_id and secret),
    )


@router.put("/credentials", response_model=CredentialsOut)
def put_credentials(payload: CredentialsIn, db: Session = Depends(get_db),
                    _: User = Depends(require_role("admin"))):
    if payload.app_id is not None:
        settings_service.set_value(db, "feishu_app_id", payload.app_id.strip())
    if payload.app_secret is not None:
        if payload.app_secret == "__CLEAR__":
            settings_service.set_value(db, "feishu_app_secret", "")
        elif payload.app_secret:
            settings_service.set_value(db, "feishu_app_secret", payload.app_secret.strip())
    db.commit()
    app_id = settings_service.get(db, "feishu_app_id", env_fallback=False) or ""
    secret = settings_service.get(db, "feishu_app_secret", env_fallback=False) or ""
    return CredentialsOut(
        app_id=app_id, app_secret_masked=settings_service.mask_secret(secret),
        configured=bool(app_id and secret),
    )


@router.post("/test")
def test_connection(db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    return feishu_client.test_connection(db)


# ----------------------------- 同步 / 冲突 ---------------------- #


class SyncIn(BaseModel):
    system_table: Optional[str] = None   # 指定则只同步这张, 否则同步所有 enabled


@router.post("/sync")
def trigger_sync(payload: SyncIn, db: Session = Depends(get_db),
                 _: User = Depends(require_role("admin", "operator"))):
    if payload.system_table:
        b = db.execute(
            select(FeishuTableBinding).where(
                FeishuTableBinding.system_table == payload.system_table)
        ).scalar_one_or_none()
        if b is None:
            raise HTTPException(404, "binding 不存在")
        results = [feishu_sync_service.sync_binding(db, b)]
    else:
        results = feishu_sync_service.sync_all(db)
    db.commit()
    return {"results": [r.__dict__ for r in results]}


class ConflictOut(BaseModel):
    id: int
    system_table: str
    source_pk: Optional[str]
    description: str
    context: Optional[dict]
    created_at: Optional[str]


@router.get("/conflicts", response_model=list[ConflictOut])
def list_conflicts(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    rows = db.execute(
        select(DataException).where(
            DataException.exception_type == "feishu_conflict",
            DataException.status == "open",
        ).order_by(DataException.id.desc())
    ).scalars().all()
    return [
        ConflictOut(
            id=e.id, system_table=e.source_table, source_pk=e.source_pk,
            description=e.description, context=e.context,
            created_at=e.created_at.isoformat() if e.created_at else None,
        )
        for e in rows
    ]


class ResolveIn(BaseModel):
    keep: str   # system | feishu


@router.post("/conflicts/{exception_id}/resolve")
def resolve_conflict(exception_id: int, payload: ResolveIn, db: Session = Depends(get_db),
                     user: User = Depends(require_role("admin", "operator"))):
    try:
        feishu_sync_service.resolve_conflict(
            db, exception_id, payload.keep, resolved_by=user.username)
    except feishu_client.FeishuError as e:
        raise HTTPException(502, f"飞书操作失败: {e}")
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return {"ok": True}
