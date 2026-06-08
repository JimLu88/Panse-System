"""结算账单(微信/聚合 billDetail)导入 + 列表 + 汇总。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.models.auth import User
from app.models.settlement import OrderSettlement
from app.services import (
    import_storage, order_reconciliation_service, recon_diagnostics_service, settlement_import_service,
)

router = APIRouter(prefix="/api/settlements", tags=["settlements"])


@router.post("/import")
def import_settlements(
    file: UploadFile = File(...),
    source: str = Query("wechat", description="wechat(聚合) / alipay"),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    content = file.file.read()
    arch = import_storage.archive(
        db, content=content, original_name=file.filename or "settlement.xlsx",
        kind="settlement", source="web", uploaded_by=getattr(_, "username", None),
    )
    result = settlement_import_service.import_bill(db, content, source=source)
    if isinstance(result, dict):
        import_storage.update_summary(db, arch.file.id, result)
        result = {**result, "archived_file_id": arch.file.id, "duplicate_upload": arch.is_duplicate}
    db.commit()
    return result


@router.get("/summary")
def get_summary(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    return settlement_import_service.summary(db)


@router.get("")
def list_settlements(
    limit: int = Query(100, le=2000),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    rows = db.execute(
        select(OrderSettlement)
        .order_by(OrderSettlement.settle_time.desc().nulls_last(), OrderSettlement.id.desc())
        .limit(limit)
    ).scalars().all()
    return [{
        "id": r.id, "source": r.source, "pay_no": r.pay_no, "order_no": r.order_no,
        "settle_time": r.settle_time.isoformat() if r.settle_time else None,
        "entry_type": r.entry_type, "income": float(r.income or 0), "expense": float(r.expense or 0),
        "description": r.description,
    } for r in rows]


@router.get("/reconciliation/summary")
def reconciliation_summary(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    """逐笔对账全量汇总: 应付/实付/补贴/实收/2%税/软件费 合计 + 对账状态分布 + 到账覆盖率。"""
    return order_reconciliation_service.summary(db)


@router.get("/reconciliation/gap")
def reconciliation_gap(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    """到账覆盖缺口诊断: 按月铺开覆盖率 + 待补金额, 指出最该补流水/账单的几个月。"""
    return order_reconciliation_service.coverage_gap(db)


@router.get("/reconciliation/diagnostics")
def reconciliation_diagnostics(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    """对账诊断: 账户余额钩稽 + 孤儿流水(没人认领的钱) + 各账户流水覆盖 (揭示对账缺口在哪)。"""
    return recon_diagnostics_service.diagnostics(db)


@router.get("/reconciliation")
def reconciliation_list(
    limit: int = Query(200, le=2000),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None, description="matched / diff / pending"),
    channel: str | None = Query(None, description="wechat / alipay / none"),
    q: str | None = Query(None, description="订单号 / 客户名 关键词"),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    """逐笔对账明细 (每单一行, 四方对账 + 2%补贴税)。"""
    return order_reconciliation_service.per_order(
        db, limit=limit, offset=offset, status=status, channel=channel, q=q,
    )
