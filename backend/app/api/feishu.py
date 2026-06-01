import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_role
from app.models.auth import User
from app.models.exception import DataException
from app.models.feishu_sync import FeishuTableBinding
from app.services import (
    feishu_client,
    feishu_preset,
    feishu_sync_service,
    feishu_webhook_service,
    settings_service,
)

router = APIRouter(prefix="/api/feishu", tags=["feishu"])
_logger = logging.getLogger("panse.feishu_sync")


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
        select(FeishuTableBinding).where(
            FeishuTableBinding.system_table == payload.system_table,
            FeishuTableBinding.feishu_table_id == payload.feishu_table_id,
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            409,
            f"binding for ({payload.system_table}, {payload.feishu_table_id}) already exists",
        )
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


class SetupPresetIn(BaseModel):
    wiki_token: str
    enabled: bool = False
    overwrite: bool = False


@router.post("/setup-preset")
def setup_preset(payload: SetupPresetIn, db: Session = Depends(get_db),
                 _: User = Depends(require_role("admin"))):
    """一键按预设创建全部表绑定 (默认不启用, 先核对字段再启用)。

    - wiki_token 解析为 Bitable App Token (若已是 bascn 开头的 app token 则直接用)。
    - 同 (system_table, feishu_table_id) 已存在: overwrite=False 跳过, True 则更新。
    """
    wiki_token = payload.wiki_token.strip()
    # Tokens starting with "bas" are already bitable app_tokens — use directly.
    if wiki_token.startswith("bas"):
        app_token = wiki_token
    else:
        try:
            app_token = feishu_client.resolve_wiki_app_token(db, wiki_token)
        except feishu_client.FeishuError as e:
            err_str = str(e)
            # If credentials are wrong, propagate immediately so the user fixes them.
            if "获取飞书 token 失败" in err_str:
                raise HTTPException(502, f"飞书操作失败: {e}")
            # For other resolution errors (wiki API unavailable, no wiki permission, etc.)
            # fall back to using the token as a direct app_token so bindings can be created
            # and corrected later via the UI.
            app_token = wiki_token

    existing = db.execute(select(FeishuTableBinding)).scalars().all()
    by_pair = {(b.system_table, b.feishu_table_id): b for b in existing}

    created = skipped = updated = 0
    items: list[dict] = []
    for p in feishu_preset.get_presets():
        key = (p["system_table"], p["feishu_table_id"])
        fm_json = json.dumps(p["field_mapping"], ensure_ascii=False)
        b = by_pair.get(key)
        if b is not None:
            if not payload.overwrite:
                skipped += 1
                action = "skipped"
            else:
                b.feishu_app_token = app_token
                b.field_mapping = fm_json
                b.direction = p["direction"]
                updated += 1
                action = "updated"
        else:
            b = FeishuTableBinding(
                system_table=p["system_table"],
                feishu_app_token=app_token,
                feishu_table_id=p["feishu_table_id"],
                direction=p["direction"],
                field_mapping=fm_json,
                enabled=payload.enabled,
            )
            db.add(b)
            by_pair[key] = b
            created += 1
            action = "created"
        items.append({
            "system_table": p["system_table"],
            "label": p["label"],
            "feishu_table_id": p["feishu_table_id"],
            "action": action,
        })

    db.commit()
    return {
        "app_token": app_token,
        "created": created,
        "skipped": skipped,
        "updated": updated,
        "items": items,
    }


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
    verification_token: Optional[str] = None  # 事件回调校验 token; "__CLEAR__" 清除
    encrypt_key: Optional[str] = None         # 事件回调 Encrypt Key; "__CLEAR__" 清除


class CredentialsOut(BaseModel):
    app_id: str
    app_secret_masked: str
    configured: bool
    verification_token_set: bool = False
    encrypt_key_set: bool = False


def _creds_out(db: Session) -> "CredentialsOut":
    app_id = settings_service.get(db, "feishu_app_id", env_fallback=False) or ""
    secret = settings_service.get(db, "feishu_app_secret", env_fallback=False) or ""
    return CredentialsOut(
        app_id=app_id,
        app_secret_masked=settings_service.mask_secret(secret),
        configured=bool(app_id and secret),
        verification_token_set=bool(settings_service.get(db, "feishu_verification_token", env_fallback=False)),
        encrypt_key_set=bool(settings_service.get(db, "feishu_encrypt_key", env_fallback=False)),
    )


@router.get("/credentials", response_model=CredentialsOut)
def get_credentials(db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    return _creds_out(db)


def _set_or_clear(db: Session, key: str, value: Optional[str]) -> None:
    if value is None:
        return
    if value == "__CLEAR__":
        settings_service.set_value(db, key, "")
    elif value:
        settings_service.set_value(db, key, value.strip())


@router.put("/credentials", response_model=CredentialsOut)
def put_credentials(payload: CredentialsIn, db: Session = Depends(get_db),
                    _: User = Depends(require_role("admin"))):
    if payload.app_id is not None:
        settings_service.set_value(db, "feishu_app_id", payload.app_id.strip())
    _set_or_clear(db, "feishu_app_secret", payload.app_secret)
    _set_or_clear(db, "feishu_verification_token", payload.verification_token)
    _set_or_clear(db, "feishu_encrypt_key", payload.encrypt_key)
    db.commit()
    return _creds_out(db)


@router.get("/resolve-wiki")
def resolve_wiki(wiki_token: str, db: Session = Depends(get_db),
                 _: User = Depends(require_role("admin"))):
    """将 Wiki 节点 token 解析为 Bitable App Token."""
    try:
        app_token = feishu_client.resolve_wiki_app_token(db, wiki_token)
    except feishu_client.FeishuError as e:
        raise HTTPException(502, f"飞书操作失败: {e}")
    return {"app_token": app_token}


@router.get("/table-fields")
def table_fields(app_token: str, table_id: str, db: Session = Depends(get_db),
                 _: User = Depends(require_role("admin"))):
    """获取 Bitable 表的字段列表."""
    try:
        fields = feishu_client.list_table_fields(db, app_token, table_id)
    except feishu_client.FeishuError as e:
        raise HTTPException(502, f"飞书操作失败: {e}")
    return {"fields": [{"field_name": f.get("field_name"), "type": f.get("type")} for f in fields]}


@router.post("/test")
def test_connection(db: Session = Depends(get_db), _: User = Depends(require_role("admin"))):
    return feishu_client.test_connection(db)


# ----------------------------- 同步 / 冲突 ---------------------- #


class SyncIn(BaseModel):
    system_table: Optional[str] = None   # 指定则只同步这张, 否则同步所有 enabled


@router.post("/sync")
def trigger_sync(payload: SyncIn, db: Session = Depends(get_db),
                 _: User = Depends(require_role("admin", "operator"))):
    """手动触发同步 → 后台执行, 立即返回。进度查 /sync/status 或运行日志。"""
    if payload.system_table:
        b = db.execute(
            select(FeishuTableBinding).where(
                FeishuTableBinding.system_table == payload.system_table)
        ).scalar_one_or_none()
        if b is None:
            raise HTTPException(404, "binding 不存在")
    _logger.info("飞书同步: 收到手动触发请求 (scope=%s)", payload.system_table or "all")
    started = feishu_sync_service.start_background_sync(payload.system_table)
    if not started:
        _logger.info("飞书同步: 已有任务在跑, 本次忽略")
        return {"status": "already_running",
                "detail": "已有同步任务在后台运行, 请等它完成或去运行日志查看进度"}
    return {"status": "started",
            "detail": "同步已在后台开始, 进度请看「管理 → 运行日志」(模块: 飞书同步)"}


@router.get("/sync/status")
def get_sync_status(_: User = Depends(get_current_user)):
    return feishu_sync_service.sync_status()


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


@router.get("/extra-fields", response_model=list[ConflictOut])
def list_extra_field_conflicts(db: Session = Depends(get_db),
                               _: User = Depends(get_current_user)):
    """飞书表比系统多出来的列 (待裁决: 删除 / 保留)。"""
    rows = db.execute(
        select(DataException).where(
            DataException.exception_type == "feishu_extra_field",
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


class ResolveExtraFieldsIn(BaseModel):
    action: str   # delete | keep


@router.post("/extra-fields/{exception_id}/resolve")
def resolve_extra_fields(exception_id: int, payload: ResolveExtraFieldsIn,
                         db: Session = Depends(get_db),
                         user: User = Depends(require_role("admin", "operator"))):
    try:
        feishu_sync_service.resolve_extra_fields(
            db, exception_id, payload.action, resolved_by=user.username)
    except feishu_client.FeishuError as e:
        raise HTTPException(502, f"飞书操作失败: {e}")
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return {"ok": True}


class ResolveIn(BaseModel):
    keep: Optional[str] = None                       # 整条裁决: system | feishu
    field_choices: Optional[dict[str, str]] = None   # 字段级合并: {字段: system|feishu}


@router.post("/conflicts/{exception_id}/resolve")
def resolve_conflict(exception_id: int, payload: ResolveIn, db: Session = Depends(get_db),
                     user: User = Depends(require_role("admin", "operator"))):
    try:
        if payload.field_choices is not None:
            feishu_sync_service.resolve_conflict_merged(
                db, exception_id, payload.field_choices, resolved_by=user.username)
        elif payload.keep is not None:
            feishu_sync_service.resolve_conflict(
                db, exception_id, payload.keep, resolved_by=user.username)
        else:
            raise ValueError("需提供 keep 或 field_choices")
    except feishu_client.FeishuError as e:
        raise HTTPException(502, f"飞书操作失败: {e}")
    except ValueError as e:
        raise HTTPException(400, str(e))
    db.commit()
    return {"ok": True}


# ----------------------------- 事件回调 (Webhook) -------------------- #


@router.get("/webhook")
async def feishu_webhook_verify(challenge: Optional[str] = None, token: Optional[str] = None,
                                 db: Session = Depends(get_db)):
    """飞书事件订阅 URL 验证 (GET 方式, 旧版 v1 API)。
    飞书开放平台配置回调地址时会发 GET ?challenge=xxx&token=xxx&type=url_verification,
    必须回 {"challenge": xxx} 才能通过验证。
    """
    expected = settings_service.get(db, "feishu_verification_token", env_fallback=False)
    if expected and token and token != expected:
        raise HTTPException(401, "verification token 不匹配")
    return {"challenge": challenge}


@router.post("/webhook")
async def feishu_webhook(request: Request, db: Session = Depends(get_db)):
    """飞书事件订阅回调入口 (公开 — 用 verification token / encrypt key 校验来源)。

    在飞书开放平台「事件订阅」填: <本服务公网地址>/api/feishu/webhook
    并订阅"多维表格记录变更"事件, 即可实现飞书改完近实时同步。
    """
    body = await request.json()
    try:
        return feishu_webhook_service.handle(db, body)
    except PermissionError as e:
        raise HTTPException(401, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
