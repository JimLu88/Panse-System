"""工厂逐单对账 API — 导入工厂侧对账单 xlsx + 逐月对账 + 逐单填原因做平。"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.models.auth import User
from app.services import factory_recon_import_service, factory_recon_service, import_storage

router = APIRouter(prefix="/api/factory-recon", tags=["factory-recon"])


@router.post("/import")
def import_factory_recon(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    """导入工厂侧对账单 xlsx (价格=工厂结算价=成本); 自动回填 Order.actual_cost。"""
    content = file.file.read()
    arch = import_storage.archive(
        db, content=content, original_name=file.filename or "factory_recon.xlsx",
        kind="factory_recon", source="web", uploaded_by=getattr(user, "username", None),
    )
    rep = factory_recon_import_service.import_factory_recon_xlsx(db, content)
    import_storage.update_summary(db, arch.file.id, {
        "inserted": rep.inserted, "skipped_duplicate": rep.skipped_duplicate,
        "backfilled_cost": rep.backfilled_cost,
    })
    db.commit()
    # 实时同步: 工厂对账单导入后自动跑全流水线(工厂流水匹配+货款对账+成本), 不用再手点
    from app.services import realtime_sync_service
    realtime_sync_service.trigger("import:factory-recon")
    return {
        "inserted": rep.inserted, "skipped_invalid": rep.skipped_invalid,
        "skipped_duplicate": rep.skipped_duplicate, "backfilled_cost": rep.backfilled_cost,
        "sheets": rep.sheets, "unmapped_columns": rep.unmapped_columns, "errors": rep.errors,
        "archived_file_id": arch.file.id, "duplicate_upload": arch.is_duplicate,
    }


@router.get("/summary")
def factory_recon_summary(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    """逐月对账汇总: 应付(Σ结算价) ↔ 实付(factory_payment) + 差额 + 已归因状态。"""
    return factory_recon_service.summary(db)


@router.get("/preview-from-orders")
def factory_recon_preview_from_orders(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    """工厂对账单未导入时的逐单预估: 用我方「工厂下单」数据(应付=账单额/理论成本), 只读不写。"""
    return factory_recon_service.preview_from_orders(db)


@router.get("/items")
def factory_recon_items(
    period: str | None = Query(None, description="YYYY-MM"),
    status: str | None = Query(None, description="resolved / open"),
    q: str | None = Query(None, description="订单号/客户/详情 关键词"),
    limit: int = Query(500, le=2000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    """工厂结算逐单明细。"""
    return factory_recon_service.list_items(
        db, period=period, status=status, q=q, limit=limit, offset=offset,
    )


@router.post("/items/{item_id}/resolve")
def resolve_factory_recon_item(
    item_id: int,
    reason: str = Body("", embed=True),
    resolved: bool = Body(True, embed=True),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    """对某条工厂结算行「填原因做平」(扣减/减免/差异原因) 或撤销做平。"""
    out = factory_recon_service.resolve(
        db, item_id, reason=reason, actor=getattr(user, "username", None), resolved=resolved,
    )
    db.commit()
    return out


@router.post("/items/{item_id}/split")
def split_factory_recon_item(
    item_id: int,
    parts: list[dict] = Body(..., embed=True,
                             description='[{"amount":"120.00","resolution_kind":"价差","remark":"..."}]'),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    """Plan L5: 差异行拆分归因 — Σ 子行金额必须 = 原行金额, 不平 → 400。"""
    try:
        out = factory_recon_service.split_item(
            db, item_id, parts=parts, actor=getattr(user, "username", None),
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    db.commit()
    return out


@router.post("/items/{item_id}/confirm")
def confirm_factory_recon_item(
    item_id: int,
    resolution_kind: str = Body(..., embed=True, description="漏单/价差/运费/补偿/其他"),
    db: Session = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    """Plan L5: 确认差异行归因 (确认人/时间落库)。"""
    try:
        out = factory_recon_service.confirm_item(
            db, item_id, resolution_kind=resolution_kind,
            actor=getattr(user, "username", None),
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    db.commit()
    return out
