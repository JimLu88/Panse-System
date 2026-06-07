"""配件返厂/退货 财务台账 API (方案C).

GET  /api/part-returns            列出返厂单 (可按 status 过滤)
GET  /api/part-returns/summary    供应商对账汇总 (待收退款/已收/维修费/报废损失)
POST /api/part-returns/{id}/settle  退款收到/费用结清 → settled (可关联支付宝流水)

返厂单的创建走 POST /api/inventory/parts/{id}/defect/resolve (处理坏件时同步生成)。
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.auth import User
from app.services import part_return_service

router = APIRouter(prefix="/api/part-returns", tags=["part-returns"])


class PartReturnOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    material_code: str
    material_name: Optional[str] = None
    warehouse: str
    qty: float
    disposition: str
    amount_kind: str
    amount: Optional[float] = None
    reason: Optional[str] = None
    supplier: Optional[str] = None
    related_purchase_no: Optional[str] = None
    alipay_flow_no: Optional[str] = None
    tracking_no: Optional[str] = None
    status: str
    actor: Optional[str] = None
    processed_at: Optional[date] = None
    remark: Optional[str] = None


class SettleIn(BaseModel):
    alipay_flow_no: Optional[str] = None
    remark: Optional[str] = None


@router.get("", response_model=list[PartReturnOut])
def list_part_returns(
    status: Optional[str] = Query(None, description="open / settled"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return [PartReturnOut.model_validate(r)
            for r in part_return_service.list_returns(db, status=status)]


@router.get("/summary")
def part_returns_summary(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    return part_return_service.summary(db)


@router.post("/{return_id}/settle", response_model=PartReturnOut)
def settle_part_return(
    return_id: int,
    payload: SettleIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        rec = part_return_service.settle(
            db, return_id,
            alipay_flow_no=payload.alipay_flow_no,
            actor=getattr(user, "username", None) or "user",
            remark=payload.remark,
        )
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    db.commit()
    return PartReturnOut.model_validate(rec)
