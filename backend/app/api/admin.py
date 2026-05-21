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
    supported_providers: list[dict]


class IntegrationConfigIn(BaseModel):
    provider: Optional[str] = Field(default=None)  # "anthropic" | "openai"
    base_url: Optional[str] = None
    api_key: Optional[str] = None  # 空 = 不改; "__CLEAR__" = 清除
    model: Optional[str] = None


class IntegrationsIn(BaseModel):
    diagnose: Optional[IntegrationConfigIn] = None
    ocr: Optional[IntegrationConfigIn] = None


class TestIn(BaseModel):
    kind: str = Field(..., pattern=r"^(diagnose|ocr)$")


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
    db.commit()
    return IntegrationsOut(
        diagnose=_read(db, "diagnose"),
        ocr=_read(db, "ocr"),
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
    _: object = Depends(require_role("admin")),
):
    """业务需求: 看门狗 — admin 可在网页上重启后端 API.

    机制: 发 SIGTERM 给当前进程 → uvicorn graceful shutdown →
          docker compose `restart: unless-stopped` 立刻拉起新进程。
    """
    if payload.confirm != "RESTART":
        raise HTTPException(400, "需要 confirm=='RESTART' 才能重启")
    from app.services import system_monitor
    pid = os.getpid()
    system_monitor.request_restart()
    return RestartOut(
        accepted=True, pid=pid,
        detail="已发 SIGTERM, ~1 秒后进程退出, Docker 自动拉起",
    )
