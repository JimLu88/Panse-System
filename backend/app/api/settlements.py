"""结算账单(微信/聚合 billDetail)导入 + 列表 + 汇总。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.models.auth import User
from app.models.settlement import OrderSettlement
from app.services import settlement_import_service

router = APIRouter(prefix="/api/settlements", tags=["settlements"])


@router.post("/import")
def import_settlements(
    file: UploadFile = File(...),
    source: str = Query("wechat", description="wechat(聚合) / alipay"),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    content = file.file.read()
    result = settlement_import_service.import_bill(db, content, source=source)
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
