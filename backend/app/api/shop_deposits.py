"""店铺/平台保证金条目 CRUD — 多店铺手动加条目, 合计并入可用资金加项。"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.models.auth import User
from app.models.shop_deposit import ShopDeposit

router = APIRouter(prefix="/api/finance/shop-deposits", tags=["shop-deposits"])


def _serialize(d: ShopDeposit) -> dict:
    return {
        "id": d.id, "platform": d.platform, "shop_name": d.shop_name,
        "amount": float(d.amount or 0), "remark": d.remark,
    }


def _to_dec(v) -> Decimal:
    try:
        return Decimal(str(v).replace(",", "").replace("¥", "").strip() or "0")
    except (InvalidOperation, ValueError):
        raise HTTPException(status_code=422, detail="金额格式不正确")


@router.get("")
def list_shop_deposits(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    rows = db.execute(
        select(ShopDeposit).order_by(ShopDeposit.platform.nulls_last(), ShopDeposit.id)
    ).scalars().all()
    total = db.execute(select(func.coalesce(func.sum(ShopDeposit.amount), 0))).scalar_one()
    return {"rows": [_serialize(d) for d in rows], "total": float(total or 0), "count": len(rows)}


@router.post("")
def create_shop_deposit(
    shop_name: str = Body(..., embed=True),
    amount=Body(0, embed=True),
    platform: str | None = Body(None, embed=True),
    remark: str | None = Body(None, embed=True),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    if not (shop_name or "").strip():
        raise HTTPException(status_code=422, detail="店铺名必填")
    d = ShopDeposit(
        shop_name=shop_name.strip(), amount=_to_dec(amount),
        platform=(platform or None), remark=(remark or None),
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return _serialize(d)


@router.put("/{deposit_id}")
def update_shop_deposit(
    deposit_id: int,
    shop_name: str | None = Body(None, embed=True),
    amount=Body(None, embed=True),
    platform: str | None = Body(None, embed=True),
    remark: str | None = Body(None, embed=True),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    d = db.get(ShopDeposit, deposit_id)
    if d is None:
        raise HTTPException(status_code=404, detail="保证金条目不存在")
    if shop_name is not None:
        if not shop_name.strip():
            raise HTTPException(status_code=422, detail="店铺名不能为空")
        d.shop_name = shop_name.strip()
    if amount is not None:
        d.amount = _to_dec(amount)
    if platform is not None:
        d.platform = platform or None
    if remark is not None:
        d.remark = remark or None
    db.commit()
    db.refresh(d)
    return _serialize(d)


@router.delete("/{deposit_id}")
def delete_shop_deposit(
    deposit_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator")),
):
    d = db.get(ShopDeposit, deposit_id)
    if d is None:
        raise HTTPException(status_code=404, detail="保证金条目不存在")
    db.delete(d)
    db.commit()
    return {"deleted": deposit_id}
