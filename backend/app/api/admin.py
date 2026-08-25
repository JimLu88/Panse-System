"""管理员后台 API.

/api/admin/integrations  AI provider 配置 (业务需求: OCR + 诊断 后台可改)
/api/admin/integrations/test  联通测试 (打一次最便宜的对话, 不写知识库)
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.services import settings_service
from app.services.ai_provider import (
    SUPPORTED_PROVIDERS,
    AiUnavailable,
    build_provider,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


class IntegrationConfigOut(BaseModel):
    provider: str
    base_url: str
    api_key_masked: str
    api_key_set: bool
    model: str


class IntegrationsOut(BaseModel):
    diagnose: IntegrationConfigOut
    ocr: IntegrationConfigOut
    custom: IntegrationConfigOut
    supported_providers: list[dict]


class IntegrationConfigIn(BaseModel):
    provider: Optional[str] = Field(default=None)  # "anthropic" | "openai"
    base_url: Optional[str] = None
    api_key: Optional[str] = None  # 空 = 不改; "__CLEAR__" = 清除
    model: Optional[str] = None


class IntegrationsIn(BaseModel):
    diagnose: Optional[IntegrationConfigIn] = None
    ocr: Optional[IntegrationConfigIn] = None
    custom: Optional[IntegrationConfigIn] = None


class TestIn(BaseModel):
    kind: str = Field(..., pattern=r"^(diagnose|ocr|custom)$")


class TestOut(BaseModel):
    ok: bool
    provider: str
    model: str
    sample: Optional[str] = None
    error: Optional[str] = None


def _read(db: Session, kind: str) -> IntegrationConfigOut:
    cfg = settings_service.get_ai_config(db, kind)
    api_key = cfg.get("api_key") or ""
    return IntegrationConfigOut(
        provider=cfg.get("provider") or "anthropic",
        base_url=cfg.get("base_url") or "",
        api_key_masked=settings_service.mask_secret(api_key),
        api_key_set=bool(api_key),
        model=cfg.get("model") or "",
    )


@router.get("/integrations", response_model=IntegrationsOut)
def get_integrations(
    db: Session = Depends(get_db),
    _: object = Depends(require_role("admin")),
):
    return IntegrationsOut(
        diagnose=_read(db, "diagnose"),
        ocr=_read(db, "ocr"),
        custom=_read(db, "custom"),
        supported_providers=list(SUPPORTED_PROVIDERS),
    )


def _apply(db: Session, kind: str, body: IntegrationConfigIn) -> None:
    if body.provider is not None:
        settings_service.set_value(db, f"ai_{kind}_provider", body.provider.strip())
    if body.base_url is not None:
        settings_service.set_value(db, f"ai_{kind}_base_url", body.base_url.strip())
    if body.model is not None:
        settings_service.set_value(db, f"ai_{kind}_model", body.model.strip())
    if body.api_key is not None:
        # 约定: "__CLEAR__" 显式清除; "" / None 不改
        if body.api_key == "__CLEAR__":
            settings_service.set_value(db, f"ai_{kind}_api_key", "")
        elif body.api_key:
            settings_service.set_value(db, f"ai_{kind}_api_key", body.api_key.strip())


@router.put("/integrations", response_model=IntegrationsOut)
def put_integrations(
    payload: IntegrationsIn,
    db: Session = Depends(get_db),
    _: object = Depends(require_role("admin")),
):
    if payload.diagnose:
        _apply(db, "diagnose", payload.diagnose)
    if payload.ocr:
        _apply(db, "ocr", payload.ocr)
    if payload.custom:
        _apply(db, "custom", payload.custom)
    db.commit()
    return IntegrationsOut(
        diagnose=_read(db, "diagnose"),
        ocr=_read(db, "ocr"),
        custom=_read(db, "custom"),
        supported_providers=list(SUPPORTED_PROVIDERS),
    )


@router.post("/integrations/test", response_model=TestOut)
def test_integration(
    payload: TestIn,
    db: Session = Depends(get_db),
    _: object = Depends(require_role("admin")),
):
    cfg = settings_service.get_ai_config(db, payload.kind)
    try:
        provider = build_provider(cfg)
    except AiUnavailable as e:
        return TestOut(ok=False, provider=cfg.get("provider", ""), model=cfg.get("model", ""),
                       error=str(e))
    try:
        resp = provider.chat(
            system="你是 ERP 配置自检助手。",
            user="请回复一个汉字「好」用来确认 API 通畅。",
            max_tokens=16,
        )
    except AiUnavailable as e:
        return TestOut(ok=False, provider=provider.name, model=provider.model, error=str(e))
    except Exception as e:  # pragma: no cover
        return TestOut(ok=False, provider=provider.name, model=provider.model,
                       error=f"{type(e).__name__}: {e}")
    return TestOut(ok=True, provider=provider.name, model=resp.model, sample=resp.text[:50])


# ------------------- 活动系统 AI (DeepSeek/千问, 2026-07-17) ---------------- #
# key 加密落库 (settings_service._SECRET_KEYS); 读取绝不回明文 — 只回 set 状态+尾4位。


class CampaignAiOut(BaseModel):
    provider: str            # none | deepseek | qwen
    model: str
    api_key_set: bool
    api_key_tail: str        # 尾 4 位; 未配置为 ""
    providers: list[dict]    # 下拉可选项 {value,label,default_model}


class CampaignAiIn(BaseModel):
    provider: Optional[str] = Field(default=None, pattern=r"^(none|deepseek|qwen)$")
    model: Optional[str] = None
    api_key: Optional[str] = None   # 空 = 不改; "__CLEAR__" = 清除 (与 integrations 同约定)


def _campaign_ai_out(db: Session) -> CampaignAiOut:
    from app.services import campaign_ai_service
    st = campaign_ai_service.settings_status(db)
    return CampaignAiOut(**st, providers=list(campaign_ai_service.PROVIDER_OPTIONS))


@router.get("/campaign-ai", response_model=CampaignAiOut)
def get_campaign_ai_settings(
    db: Session = Depends(get_db),
    _: object = Depends(require_role("admin")),
):
    return _campaign_ai_out(db)


@router.put("/campaign-ai", response_model=CampaignAiOut)
def put_campaign_ai_settings(
    payload: CampaignAiIn,
    db: Session = Depends(get_db),
    _: object = Depends(require_role("admin")),
):
    if payload.provider is not None:
        settings_service.set_value(db, "campaign_ai_provider", payload.provider.strip().lower())
    if payload.model is not None:
        settings_service.set_value(db, "campaign_ai_model", payload.model.strip())
    if payload.api_key is not None:
        if payload.api_key == "__CLEAR__":
            settings_service.set_value(db, "campaign_ai_api_key", "")
        elif payload.api_key:
            settings_service.set_value(db, "campaign_ai_api_key", payload.api_key.strip())
    db.commit()
    return _campaign_ai_out(db)


@router.post("/campaign-ai/test", response_model=TestOut)
def test_campaign_ai(
    db: Session = Depends(get_db),
    _: object = Depends(require_role("admin")),
):
    from app.services import campaign_ai_service
    provider = campaign_ai_service.get_campaign_ai(db)
    if provider is None:
        return TestOut(ok=False, provider="none", model="",
                       error="活动系统 AI 未配置 (provider=none 或 API Key 为空)")
    try:
        resp = provider.chat(
            system="你是 ERP 配置自检助手。",
            user="请回复一个汉字「好」用来确认 API 通畅。",
            max_tokens=16,
        )
    except AiUnavailable as e:
        return TestOut(ok=False, provider=provider.name, model=provider.model, error=str(e))
    except Exception as e:  # pragma: no cover
        return TestOut(ok=False, provider=provider.name, model=provider.model,
                       error=f"{type(e).__name__}: {e}")
    return TestOut(ok=True, provider=provider.name, model=resp.model, sample=resp.text[:50])


# ----------------------------- 系统监控 / 看门狗 (业务需求) ---------- #


class HealthCheckOut(BaseModel):
    name: str
    status: str
    detail: str
    duration_ms: int


class SystemStatusOut(BaseModel):
    uptime_sec: int
    process_started_at: str
    version_sha: str
    python_version: str
    db_ok: bool
    db_latency_ms: Optional[int]
    pending_migrations: int
    disk_total_gb: float
    disk_free_gb: float
    disk_used_pct: float
    mem_total_mb: int
    mem_available_mb: int
    mem_used_pct: float
    storage_used_mb: int
    recent_checks: list[HealthCheckOut]


@router.get("/system-status", response_model=SystemStatusOut)
def get_system_status(
    db: Session = Depends(get_db),
    _: object = Depends(require_role("admin")),
):
    from app.services import system_monitor
    s = system_monitor.get_status(db)
    return SystemStatusOut(
        uptime_sec=s.uptime_sec,
        process_started_at=s.process_started_at,
        version_sha=s.version_sha,
        python_version=s.python_version,
        db_ok=s.db_ok,
        db_latency_ms=s.db_latency_ms,
        pending_migrations=s.pending_migrations,
        disk_total_gb=s.disk_total_gb,
        disk_free_gb=s.disk_free_gb,
        disk_used_pct=s.disk_used_pct,
        mem_total_mb=s.mem_total_mb,
        mem_available_mb=s.mem_available_mb,
        mem_used_pct=s.mem_used_pct,
        storage_used_mb=s.storage_used_mb,
        recent_checks=[
            HealthCheckOut(name=c.name, status=c.status,
                           detail=c.detail, duration_ms=c.duration_ms)
            for c in s.recent_checks
        ],
    )


class OwnerHealthOut(BaseModel):
    open_exceptions: int
    exceptions_by_severity: dict
    failing_jobs: list[str]
    latest_backup_age_h: Optional[float] = None
    latest_backup_size_mb: Optional[float] = None
    backup_stale: Optional[bool] = None
    healthy: bool


def _backup_status() -> dict:
    """读 /backups 最新备份的新鲜度 (优化 #4)。无挂载/无备份则字段为空/标陈旧。"""
    import os
    import time
    d = os.environ.get("BACKUP_DIR", "/backups")
    out: dict = {"latest_backup_age_h": None, "latest_backup_size_mb": None, "backup_stale": None}
    try:
        files = [f for f in os.listdir(d) if f.startswith("panse-") and f.endswith(".sql.gz")]
        if not files:
            out["backup_stale"] = True
            return out
        newest = max(files, key=lambda f: os.path.getmtime(os.path.join(d, f)))
        p = os.path.join(d, newest)
        out["latest_backup_age_h"] = round((time.time() - os.path.getmtime(p)) / 3600, 1)
        out["latest_backup_size_mb"] = round(os.path.getsize(p) / 1024 / 1024, 2)
        out["backup_stale"] = out["latest_backup_age_h"] > 36 or out["latest_backup_size_mb"] < 0.001
    except OSError:
        pass
    return out


@router.get("/owner-health", response_model=OwnerHealthOut)
def owner_health(
    db: Session = Depends(get_db),
    _: object = Depends(require_role("admin", "operator")),
):
    """一页体检 (优化 #10): 待处理异常 + 最近一次运行失败的定时任务。补在现有系统监控里,
    回答"系统现在有没有要紧的事要处理", 不与技术监控(磁盘/内存/迁移)重复。"""
    from sqlalchemy import func, select
    from app.models.exception import DataException
    from app.models.scheduled_job import ScheduledJobRun
    total_open = db.execute(
        select(func.count(DataException.id)).where(DataException.status == "open")
    ).scalar() or 0
    by_sev = {
        str(s): int(c) for s, c in db.execute(
            select(DataException.severity, func.count(DataException.id))
            .where(DataException.status == "open").group_by(DataException.severity)
        ).all()
    }
    latest: dict[str, str] = {}
    for jid, st in db.execute(
        select(ScheduledJobRun.job_id, ScheduledJobRun.status)
        .order_by(ScheduledJobRun.id.desc()).limit(200)
    ).all():
        latest.setdefault(jid, st)
    failing = [jid for jid, st in latest.items() if st == "fail"]
    bk = _backup_status()
    return OwnerHealthOut(
        open_exceptions=int(total_open), exceptions_by_severity=by_sev,
        failing_jobs=failing,
        latest_backup_age_h=bk["latest_backup_age_h"],
        latest_backup_size_mb=bk["latest_backup_size_mb"],
        backup_stale=bk["backup_stale"],
        healthy=(total_open == 0 and not failing and not bk["backup_stale"]),
    )


class HealthLogOut(BaseModel):
    id: int
    check_name: str
    status: str
    detail: Optional[str]
    duration_ms: Optional[int]
    created_at: str


@router.get("/system-health-logs", response_model=list[HealthLogOut])
def get_health_logs(
    limit: int = 100,
    check_name: Optional[str] = None,
    db: Session = Depends(get_db),
    _: object = Depends(require_role("admin")),
):
    from app.services import system_monitor
    rows = system_monitor.recent_logs(db, limit=limit, check_name=check_name)
    return [
        HealthLogOut(
            id=r.id, check_name=r.check_name, status=r.status,
            detail=r.detail, duration_ms=r.duration_ms,
            created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]


class RestartIn(BaseModel):
    confirm: str = Field(..., description="必须传 'RESTART' 字面值, 防误操作")


class RestartOut(BaseModel):
    accepted: bool
    pid: int
    detail: str


@router.post("/restart-api", response_model=RestartOut)
def restart_api(
    payload: RestartIn,
    db: Session = Depends(get_db),
    user = Depends(require_role("admin")),
):
    """业务需求: 看门狗 — admin 可在网页上重启后端 API.

    机制: 发 SIGTERM 给当前进程 → uvicorn graceful shutdown →
          docker compose `restart: unless-stopped` 立刻拉起新进程。
    """
    if payload.confirm != "RESTART":
        raise HTTPException(400, "需要 confirm=='RESTART' 才能重启")
    from app.services import system_monitor
    pid = os.getpid()
    system_monitor.request_restart(
        db, actor=getattr(user, "username", None) or "admin",
        detail="admin 在网页点击 “重启 API” 按钮",
    )
    return RestartOut(
        accepted=True, pid=pid,
        detail="已发 SIGTERM, ~1 秒后进程退出, Docker 自动拉起",
    )


# ----------------------------- 事件日志 (业务需求 5) ----------------- #


class SystemEventOut(BaseModel):
    id: int
    kind: str
    actor: Optional[str]
    detail: Optional[str]
    snapshot_json: Optional[dict]
    created_at: str


@router.get("/system-events", response_model=list[SystemEventOut])
def get_system_events(
    limit: int = 50,
    db: Session = Depends(get_db),
    _: object = Depends(require_role("admin")),
):
    """重启 / 看门狗 / 进程启动事件日志, UI 用来展示 diff."""
    from app.services import system_monitor
    rows = system_monitor.recent_events(db, limit=limit)
    return [
        SystemEventOut(
            id=r.id, kind=r.kind, actor=r.actor, detail=r.detail,
            snapshot_json=r.snapshot_json,
            created_at=r.created_at.isoformat(),
        )
        for r in rows
    ]


# ----------------------------- 通知配置 (业务需求扩展) ---------------- #


class NotifyConfigOut(BaseModel):
    provider: str
    webhook_masked: str
    webhook_set: bool
    supported_providers: list[dict]
    text_channels: str  # 纯文本通知渠道, 逗号分隔 (feishu=飞书应用机器人 / webhook=notify provider,如企微)
    route_mode: str
    feishu_order_chat_id_masked: str
    feishu_order_chat_set: bool
    feishu_alert_chat_id_masked: str
    feishu_alert_chat_set: bool


class NotifyConfigIn(BaseModel):
    provider: Optional[str] = Field(default=None,
                                    description="slack/wechat_work/dingtalk/feishu/none")
    webhook: Optional[str] = Field(default=None, description='完整 URL; "__CLEAR__" 清空')
    text_channels: Optional[str] = Field(default=None, description="纯文本通知渠道 feishu,webhook 逗号分隔")
    route_mode: Optional[str] = Field(default=None, description="legacy/feishu_split")
    feishu_alert_chat_id: Optional[str] = Field(default=None, description='飞书提醒群 chat_id; "__CLEAR__" 清空')


class NotifyTestOut(BaseModel):
    ok: bool
    detail: str


def _read_notify(db: Session) -> NotifyConfigOut:
    from app.services import notify_service
    cfg = notify_service.get_config(db)
    order_chat_id = settings_service.get(db, "feishu_push_chat_id", env_fallback=False) or ""
    return NotifyConfigOut(
        provider=cfg["provider"],
        webhook_masked=settings_service.mask_secret(cfg["webhook"]),
        webhook_set=cfg["webhook_set"],
        supported_providers=list(notify_service.SUPPORTED_PROVIDERS),
        text_channels=settings_service.get(db, "notify_text_channels", env_fallback=False)
        or notify_service.DEFAULT_TEXT_CHANNELS,
        route_mode=cfg["route_mode"],
        feishu_order_chat_id_masked=settings_service.mask_secret(order_chat_id),
        feishu_order_chat_set=bool(order_chat_id),
        feishu_alert_chat_id_masked=settings_service.mask_secret(cfg["alert_chat_id"]),
        feishu_alert_chat_set=cfg["alert_chat_set"],
    )


@router.get("/notify-config", response_model=NotifyConfigOut)
def get_notify_config(
    db: Session = Depends(get_db),
    _: object = Depends(require_role("admin")),
):
    return _read_notify(db)


@router.put("/notify-config", response_model=NotifyConfigOut)
def put_notify_config(
    payload: NotifyConfigIn,
    db: Session = Depends(get_db),
    _: object = Depends(require_role("admin")),
):
    from app.services import notify_service

    current = notify_service.get_config(db)
    route_mode = (payload.route_mode or current["route_mode"]).strip()
    if route_mode not in {
        notify_service.ROUTE_MODE_LEGACY,
        notify_service.ROUTE_MODE_FEISHU_SPLIT,
    }:
        raise HTTPException(status_code=422, detail="route_mode 仅支持 legacy/feishu_split")
    alert_chat_id = current["alert_chat_id"]
    if payload.feishu_alert_chat_id is not None:
        if payload.feishu_alert_chat_id == "__CLEAR__":
            alert_chat_id = ""
        elif payload.feishu_alert_chat_id.strip():
            alert_chat_id = payload.feishu_alert_chat_id.strip()
    order_chat_id = settings_service.get(db, "feishu_push_chat_id", env_fallback=False) or ""
    if route_mode == notify_service.ROUTE_MODE_FEISHU_SPLIT:
        if not order_chat_id:
            raise HTTPException(status_code=400, detail="启用双群分流前必须先配置飞书订单群")
        if not alert_chat_id:
            raise HTTPException(status_code=400, detail="启用双群分流前必须先配置飞书提醒群")

    if payload.provider is not None:
        settings_service.set_value(db, "notify_provider", payload.provider.strip())
    if payload.webhook is not None:
        if payload.webhook == "__CLEAR__":
            settings_service.set_value(db, "notify_webhook", "")
        elif payload.webhook:
            settings_service.set_value(db, "notify_webhook", payload.webhook.strip())
    if payload.text_channels is not None:
        settings_service.set_value(db, "notify_text_channels", payload.text_channels.strip())
    if payload.feishu_alert_chat_id == "__CLEAR__" or (
        payload.feishu_alert_chat_id is not None
        and payload.feishu_alert_chat_id.strip()
    ):
        settings_service.set_value(db, notify_service.ALERT_CHAT_KEY, alert_chat_id)
    if payload.route_mode is not None:
        settings_service.set_value(db, notify_service.ROUTE_MODE_KEY, route_mode)
    db.commit()
    return _read_notify(db)


@router.post("/notify-config/test", response_model=NotifyTestOut)
def test_notify_config(
    db: Session = Depends(get_db),
    _: object = Depends(require_role("admin")),
):
    from app.services import notify_service
    ok, detail = notify_service.test_notify(db)
    return NotifyTestOut(ok=ok, detail=detail)


# ----------------------------- 企业微信入站密码 ------------------- #


class WechatInboundConfigOut(BaseModel):
    enabled: bool
    corp_id: str
    token_set: bool
    aes_key_set: bool
    allowed_users: list[str]
    ready: bool
    callback_path: str
    aibot_enabled: bool
    aibot_token_set: bool
    aibot_aes_key_set: bool
    aibot_name: str
    aibot_ready: bool
    aibot_callback_path: str


class WechatInboundConfigIn(BaseModel):
    enabled: Optional[bool] = None
    corp_id: Optional[str] = Field(default=None, description='企业 ID; "__CLEAR__" 清空')
    token: Optional[str] = Field(default=None, description='回调 Token; "__CLEAR__" 清空')
    aes_key: Optional[str] = Field(default=None, description='EncodingAESKey; "__CLEAR__" 清空')
    allowed_users: Optional[list[str]] = Field(
        default=None,
        description="允许提交发货密码的企业微信成员 UserID 白名单",
    )
    aibot_enabled: Optional[bool] = None
    aibot_token: Optional[str] = Field(
        default=None, description='智能机器人回调 Token; "__CLEAR__" 清空',
    )
    aibot_aes_key: Optional[str] = Field(
        default=None, description='智能机器人 EncodingAESKey; "__CLEAR__" 清空',
    )
    aibot_name: Optional[str] = Field(
        default=None, description="智能机器人名称，用于安全移除群聊 @ 前缀",
    )


def _read_wechat_inbound(db: Session) -> WechatInboundConfigOut:
    from app.services import wechat_inbound_service

    cfg = wechat_inbound_service.get_config(db)
    aibot_cfg = wechat_inbound_service.get_aibot_config(db)
    return WechatInboundConfigOut(
        enabled=cfg["enabled"],
        corp_id=cfg["corp_id"],
        token_set=bool(cfg["token"]),
        aes_key_set=bool(cfg["aes_key"]),
        allowed_users=cfg["allowed_users"],
        ready=cfg["ready"],
        callback_path=wechat_inbound_service.CALLBACK_PATH,
        aibot_enabled=aibot_cfg["enabled"],
        aibot_token_set=bool(aibot_cfg["token"]),
        aibot_aes_key_set=bool(aibot_cfg["aes_key"]),
        aibot_name=aibot_cfg["bot_name"],
        aibot_ready=aibot_cfg["ready"],
        aibot_callback_path=wechat_inbound_service.AIBOT_CALLBACK_PATH,
    )


@router.get("/wechat-inbound-config", response_model=WechatInboundConfigOut)
def get_wechat_inbound_config(
    db: Session = Depends(get_db),
    _: object = Depends(require_role("admin")),
):
    return _read_wechat_inbound(db)


@router.put("/wechat-inbound-config", response_model=WechatInboundConfigOut)
def put_wechat_inbound_config(
    payload: WechatInboundConfigIn,
    db: Session = Depends(get_db),
    _: object = Depends(require_role("admin")),
):
    from app.services import wechat_inbound_service

    current = wechat_inbound_service.get_config(db)
    current_aibot = wechat_inbound_service.get_aibot_config(db)

    def proposed(value: Optional[str], old: str) -> str:
        if value is None or value == "":
            return old
        if value == "__CLEAR__":
            return ""
        return value.strip()

    next_cfg = {
        "enabled": current["enabled"] if payload.enabled is None else payload.enabled,
        "corp_id": proposed(payload.corp_id, current["corp_id"]),
        "token": proposed(payload.token, current["token"]),
        "aes_key": proposed(payload.aes_key, current["aes_key"]),
        "allowed_users": current["allowed_users"],
    }
    if payload.allowed_users is not None:
        next_cfg["allowed_users"] = sorted({
            item.strip() for item in payload.allowed_users if item.strip()
        })
    if len(next_cfg["corp_id"]) > 128 or len(next_cfg["token"]) > 256:
        raise HTTPException(status_code=400, detail="企业 ID 或 Token 长度无效")
    if any("," in item or len(item) > 128 for item in next_cfg["allowed_users"]):
        raise HTTPException(status_code=400, detail="成员 UserID 格式无效")
    try:
        if next_cfg["aes_key"]:
            wechat_inbound_service.validate_aes_key(next_cfg["aes_key"])
        if next_cfg["enabled"]:
            wechat_inbound_service.validate_config(next_cfg)
    except wechat_inbound_service.WechatInboundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    next_aibot = {
        "enabled": (
            current_aibot["enabled"]
            if payload.aibot_enabled is None
            else payload.aibot_enabled
        ),
        "token": proposed(payload.aibot_token, current_aibot["token"]),
        "aes_key": proposed(payload.aibot_aes_key, current_aibot["aes_key"]),
        "bot_name": proposed(payload.aibot_name, current_aibot["bot_name"]),
        "allowed_users": next_cfg["allowed_users"],
    }
    if len(next_aibot["token"]) > 256 or len(next_aibot["bot_name"]) > 128:
        raise HTTPException(status_code=400, detail="智能机器人 Token 或名称长度无效")
    try:
        if next_aibot["aes_key"]:
            wechat_inbound_service.validate_aes_key(next_aibot["aes_key"])
        if next_aibot["enabled"]:
            wechat_inbound_service.validate_aibot_config(next_aibot)
    except wechat_inbound_service.WechatInboundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    settings_service.set_value(db, wechat_inbound_service.KEY_ENABLED,
                               "true" if next_cfg["enabled"] else "false")
    settings_service.set_value(db, wechat_inbound_service.KEY_CORP_ID, next_cfg["corp_id"])
    settings_service.set_value(db, wechat_inbound_service.KEY_TOKEN, next_cfg["token"])
    settings_service.set_value(db, wechat_inbound_service.KEY_AES_KEY, next_cfg["aes_key"])
    settings_service.set_value(
        db,
        wechat_inbound_service.KEY_ALLOWED_USERS,
        ",".join(next_cfg["allowed_users"]),
    )
    settings_service.set_value(
        db,
        wechat_inbound_service.KEY_AIBOT_ENABLED,
        "true" if next_aibot["enabled"] else "false",
    )
    settings_service.set_value(db, wechat_inbound_service.KEY_AIBOT_TOKEN,
                               next_aibot["token"])
    settings_service.set_value(db, wechat_inbound_service.KEY_AIBOT_AES_KEY,
                               next_aibot["aes_key"])
    settings_service.set_value(db, wechat_inbound_service.KEY_AIBOT_NAME,
                               next_aibot["bot_name"])
    db.commit()
    return _read_wechat_inbound(db)


# ----------------------------- 物流追踪配置 (快递100) --------------- #


class LogisticsConfigOut(BaseModel):
    provider: str                       # 当前选择: kuaidi100 | kdniao | auto
    # 快递100
    customer: str
    customer_set: bool
    key_masked: str
    key_set: bool
    # 快递鸟
    kdniao_ebusiness_id: str
    kdniao_ebusiness_id_set: bool
    kdniao_key_masked: str
    kdniao_key_set: bool


class LogisticsConfigIn(BaseModel):
    provider: Optional[str] = None              # kuaidi100 | kdniao | auto
    customer: Optional[str] = None              # 快递100 customer; "__CLEAR__" 清空
    key: Optional[str] = None                   # 快递100 key; "__CLEAR__" 清空
    kdniao_ebusiness_id: Optional[str] = None   # 快递鸟 EBusinessID; "__CLEAR__" 清空
    kdniao_key: Optional[str] = None            # 快递鸟 ApiKey; "__CLEAR__" 清空


def _mask_id(v: str) -> str:
    return (v[:4] + "***") if len(v) > 4 else ("***" if v else "")


def _logistics_config_out(db: Session) -> LogisticsConfigOut:
    customer = settings_service.get(db, "kuaidi100_customer", env_fallback=True) or ""
    key = settings_service.get(db, "kuaidi100_key", env_fallback=True) or ""
    eid = settings_service.get(db, "kdniao_ebusiness_id", env_fallback=True) or ""
    kkey = settings_service.get(db, "kdniao_api_key", env_fallback=True) or ""
    provider = (settings_service.get(db, "tracking_provider", env_fallback=True) or "auto").lower()
    return LogisticsConfigOut(
        provider=provider,
        customer=_mask_id(customer),
        customer_set=bool(customer),
        key_masked=settings_service.mask_secret(key),
        key_set=bool(key),
        kdniao_ebusiness_id=_mask_id(eid),
        kdniao_ebusiness_id_set=bool(eid),
        kdniao_key_masked=settings_service.mask_secret(kkey),
        kdniao_key_set=bool(kkey),
    )


@router.get("/logistics-config", response_model=LogisticsConfigOut)
def get_logistics_config(
    db: Session = Depends(get_db),
    _: object = Depends(require_role("admin")),
):
    return _logistics_config_out(db)


@router.put("/logistics-config", response_model=LogisticsConfigOut)
def put_logistics_config(
    payload: LogisticsConfigIn,
    db: Session = Depends(get_db),
    _: object = Depends(require_role("admin")),
):
    def _apply(name: str, val: Optional[str]) -> None:
        if val is None:
            return
        settings_service.set_value(db, name, "" if val == "__CLEAR__" else val.strip())

    if payload.provider is not None:
        prov = payload.provider.strip().lower()
        if prov not in ("kuaidi100", "kdniao", "auto"):
            raise HTTPException(400, "provider 必须是 kuaidi100 / kdniao / auto")
        settings_service.set_value(db, "tracking_provider", prov)
    _apply("kuaidi100_customer", payload.customer)
    _apply("kuaidi100_key", payload.key)
    _apply("kdniao_ebusiness_id", payload.kdniao_ebusiness_id)
    _apply("kdniao_api_key", payload.kdniao_key)
    db.commit()
    return _logistics_config_out(db)


# ----------------------------- 数据水位线 (Phase 7) ----------------- #


class BaselineOut(BaseModel):
    baseline: Optional[str]


class BaselineIn(BaseModel):
    baseline: str = Field(..., description="YYYY-MM-DD; 空字符串清除")


@router.get("/data-baseline", response_model=BaselineOut)
def get_baseline(
    db: Session = Depends(get_db),
    _: object = Depends(require_role("admin")),
):
    from app.services import baseline_service
    d = baseline_service.get_baseline_date(db)
    return BaselineOut(baseline=d.isoformat() if d else None)


@router.put("/data-baseline")
def set_baseline(
    payload: BaselineIn,
    db: Session = Depends(get_db),
    user = Depends(require_role("admin")),
):
    """业务: 设置历史数据水位线. 之前的订单全部标 is_historical, 不进对账/财务公式."""
    from datetime import datetime as _dt
    from app.services import baseline_service
    if not payload.baseline:
        baseline_service.clear_baseline(db)
        db.commit()
        return {"cleared": True}
    try:
        bd = _dt.strptime(payload.baseline, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "baseline 必须是 YYYY-MM-DD")
    result = baseline_service.set_baseline_date(
        db, bd, actor=getattr(user, "username", "admin"),
    )
    db.commit()
    return result


# ----------------------------- 清空业务数据 ---------------------- #


class ResetTablesOut(BaseModel):
    tables: list[str]


class ResetDataIn(BaseModel):
    password: str = Field(..., description="当前管理员密码, 用于二次验证")
    confirm: str = Field(..., description='必须等于 "DELETE" 才执行')
    clear_feishu: bool = Field(False, description="是否同时清空飞书云端绑定表的记录 (高危, 不可逆)")
    confirm_feishu: str = Field("", description='clear_feishu 时必须等于 "DELETE FEISHU"')


class ResetDataOut(BaseModel):
    cleared: bool
    total_deleted: int
    deleted: dict[str, int]
    feishu_cleared: bool = False
    feishu_deleted: dict[str, int] = {}
    feishu_error: Optional[str] = None


@router.get("/reset-data/tables", response_model=ResetTablesOut)
def reset_data_tables(
    _: object = Depends(require_role("admin")),
):
    """返回「清空数据」会清掉的业务表清单 (设置/配置/账号不在内)。"""
    from app.services import data_reset_service
    return ResetTablesOut(tables=data_reset_service.list_business_tables())


@router.post("/reset-data", response_model=ResetDataOut)
def reset_data(
    payload: ResetDataIn,
    db: Session = Depends(get_db),
    user=Depends(require_role("admin")),
):
    """清空所有导入的业务数据, 保留账号/设置/配置。

    安全措施:
      - 仅 admin 角色
      - 必须重新输入当前管理员密码 (二次验证)
      - confirm 字段必须等于 "DELETE"
    """
    from app.services import auth_service, data_reset_service

    if payload.confirm != "DELETE":
        raise HTTPException(400, 'confirm 必须等于 "DELETE"')
    if not auth_service.verify_password(payload.password, user.password_hash):
        raise HTTPException(403, "密码错误, 清空操作已取消")

    # 飞书云端清空 (可选, 高危): 需额外二次确认
    feishu_cleared = False
    feishu_deleted: dict[str, int] = {}
    feishu_error: Optional[str] = None
    if payload.clear_feishu:
        if payload.confirm_feishu != "DELETE FEISHU":
            raise HTTPException(400, 'clear_feishu 时 confirm_feishu 必须等于 "DELETE FEISHU"')
        try:
            result = data_reset_service.reset_feishu_data(db)
            feishu_deleted = result.get("deleted", {})
            feishu_cleared = True
            # 逐表删除失败 (HTTP 没抛异常但飞书返回错误): 必须回传, 否则界面误报"成功"
            errs = result.get("errors") or {}
            if errs:
                feishu_error = "部分表删除失败: " + "; ".join(
                    f"{t}: {m}" for t, m in errs.items()
                )
        except Exception as e:  # 飞书未配置 / API 失败: 不阻断本地清空, 回传错误
            feishu_error = str(e)

    deleted = data_reset_service.reset_business_data(db)
    return ResetDataOut(
        cleared=True,
        total_deleted=sum(deleted.values()),
        deleted=deleted,
        feishu_cleared=feishu_cleared,
        feishu_deleted=feishu_deleted,
        feishu_error=feishu_error,
    )


# ─────────────────────────── 数据备份 / 导出 ─────────────────────────── #


class BackupConfigIn(BaseModel):
    auto_enabled: Optional[bool] = None
    interval_days: Optional[int] = Field(None, ge=1, le=365)
    dir: Optional[str] = None
    start_date: Optional[str] = Field(None, description="YYYY-MM-DD, 留空则不限")


class BackupConfigOut(BaseModel):
    auto_enabled: bool
    interval_days: int
    dir: str
    start_date: Optional[str] = None
    last_run_at: Optional[str] = None
    next_run_at: Optional[str] = None
    max_backups: int


class BackupFile(BaseModel):
    filename: str
    size_mb: float
    created_at: str


class BackupRunOut(BaseModel):
    file: str
    size_mb: float
    deleted_old: int
    uploaded_s3: bool


@router.get("/backup/config", response_model=BackupConfigOut)
def backup_get_config(
    db: Session = Depends(get_db),
    _: object = Depends(require_role("admin")),
):
    from app.services import backup_service
    return BackupConfigOut(**backup_service.get_config(db))


@router.put("/backup/config", response_model=BackupConfigOut)
def backup_set_config(
    payload: BackupConfigIn,
    db: Session = Depends(get_db),
    _: object = Depends(require_role("admin")),
):
    from app.services import backup_service
    cfg = backup_service.set_config(
        db,
        auto_enabled=payload.auto_enabled,
        interval_days=payload.interval_days,
        dir=payload.dir,
        start_date=payload.start_date,
    )
    return BackupConfigOut(**cfg)


@router.get("/backup/list", response_model=list[BackupFile])
def backup_list(
    db: Session = Depends(get_db),
    _: object = Depends(require_role("admin")),
):
    from app.services import backup_service
    cfg = backup_service.get_config(db)
    return [BackupFile(**f) for f in backup_service.list_backups(cfg["dir"])]


@router.post("/backup/run", response_model=BackupRunOut)
def backup_run_now(
    db: Session = Depends(get_db),
    _: object = Depends(require_role("admin")),
):
    """立即全量导出一份 Excel 备份到备份目录 (含轮换 + 可选 S3)。"""
    from app.services import backup_service
    cfg = backup_service.get_config(db)
    result = backup_service.run(db, output_dir=cfg["dir"])
    return BackupRunOut(**result)


@router.get("/backup/download/{filename}")
def backup_download(
    filename: str,
    db: Session = Depends(get_db),
    _: object = Depends(require_role("admin")),
):
    """下载某个备份文件。也可用于「一键导出并下载」: 先 /backup/run 再下载返回的文件名。"""
    from pathlib import Path

    from fastapi.responses import FileResponse

    from app.services import backup_service

    # 防路径穿越: 仅允许 panse_backup_*.xlsx 形式的纯文件名
    if "/" in filename or "\\" in filename or not filename.startswith("panse_backup_") \
            or not filename.endswith(".xlsx"):
        raise HTTPException(400, "非法文件名")
    cfg = backup_service.get_config(db)
    path = (Path(cfg["dir"]) / filename).resolve()
    if Path(cfg["dir"]).resolve() not in path.parents or not path.is_file():
        raise HTTPException(404, "备份文件不存在")
    return FileResponse(
        str(path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
    )
