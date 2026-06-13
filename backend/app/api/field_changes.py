"""人工编辑历史档案 API (方向 2+4)。

GET /api/field-changes/history — 某表某行某字段最近 30 份 (字段悬浮历史)
GET /api/field-changes          — 修改档案中心 (按 人/表/来源/关键词 检索)
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.models.auth import User
from app.services import field_change_service

router = APIRouter(prefix="/api/field-changes", tags=["field-changes"])


@router.get("/history")
def field_history(
    table: str = Query(..., description="表名, 如 pricing_skus"),
    pk: str = Query(..., description="行业务主键, 如 sku_code"),
    field: str = Query(..., description="字段名"),
    limit: int = Query(30, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    """字段级悬浮历史: 该字段最近 N 份修改 (新→旧, 带日期/人/来源)。"""
    return {"rows": field_change_service.history(db, table=table, pk=pk, field=field, limit=limit)}


@router.get("")
def list_changes(
    table: Optional[str] = None,
    pk: Optional[str] = None,
    actor: Optional[str] = None,
    source: Optional[str] = Query(None, description="web / feishu"),
    q: Optional[str] = Query(None, description="行/字段 关键词"),
    limit: int = Query(200, le=1000),
    offset: int = 0,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    """修改档案中心: 全局人工修改流水。"""
    return field_change_service.recent(
        db, table=table, pk=pk, actor=actor, source=source,
        q=q, limit=limit, offset=offset,
    )
