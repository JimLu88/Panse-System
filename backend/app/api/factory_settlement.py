"""木作工厂月结销账 API (用户 2026-07-01)。

GET    /api/factory-settlement/overview          月度欠款台账 + 销账记录 + 别名
POST   /api/factory-settlement/settle            某月一键「已付清」(手动销账)
POST   /api/factory-settlement/reverse/{pid}     撤销一笔销账
GET    /api/factory-settlement/aliases           供应商别名列表
POST   /api/factory-settlement/aliases           加别名
DELETE /api/factory-settlement/aliases/{id}      删别名
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.models.auth import User
from app.models.factory_settlement import DEFAULT_WOOD_SUPPLIER
from app.services import factory_settlement_service as fss

router = APIRouter(prefix="/api/factory-settlement", tags=["factory-settlement"])


class SettleIn(BaseModel):
    month: str                       # "YYYY-MM"
    supplier: Optional[str] = None
    paid_amount: Optional[Decimal] = None
    flow_no: Optional[str] = None
    note: Optional[str] = None


class AliasIn(BaseModel):
    alias: str
    supplier: Optional[str] = None
    note: Optional[str] = None


@router.get("/overview")
def overview(supplier: Optional[str] = None, db: Session = Depends(get_db),
             _: User = Depends(require_role("admin", "operator"))):
    sup = supplier or DEFAULT_WOOD_SUPPLIER
    return {
        "breakdown": fss.month_breakdown(db, sup),
        "payments": fss.list_payments(db, sup),
        "aliases": fss.list_aliases(db),
    }


@router.post("/settle")
def settle(payload: SettleIn, db: Session = Depends(get_db),
           user: User = Depends(require_role("admin", "operator"))):
    sup = payload.supplier or DEFAULT_WOOD_SUPPLIER
    res = fss.settle_month(
        db, supplier=sup, month=payload.month, trigger="manual",
        flow_no=payload.flow_no, paid_amount=payload.paid_amount,
        by=getattr(user, "username", None), note=payload.note,
    )
    db.commit()
    if not res.get("flipped"):
        # 该月已无未付单(可能已全部付清), 不报错, 让前端提示
        res["message"] = "该月已无未付的已开账单(可能已全部付清)"
    return res


@router.post("/reverse/{payment_id}")
def reverse(payment_id: int, db: Session = Depends(get_db),
            user: User = Depends(require_role("admin", "operator"))):
    res = fss.reverse_settlement(db, payment_id, by=getattr(user, "username", None))
    if res.get("error"):
        raise HTTPException(400, res["error"])
    db.commit()
    return res


@router.post("/scan-alipay")
def scan_alipay(db: Session = Depends(get_db),
                _: User = Depends(require_role("admin", "operator"))):
    """手动扫支付宝出账: 识别木作货款(纠正归类)+ 备注「X月已付清」自动销账。"""
    res = fss.route_alipay_settlements(db)
    db.commit()
    return res


@router.get("/missing-orders")
def missing_orders(up_to_month: Optional[str] = None, supplier: Optional[str] = None,
                   db: Session = Depends(get_db),
                   _: User = Depends(require_role("admin", "operator"))):
    """漏单: 已发货但未被任何工厂账单覆盖的单(按发货月累计到 up_to_month)。"""
    return fss.missing_orders(db, up_to_month=up_to_month, supplier=supplier or DEFAULT_WOOD_SUPPLIER)


@router.get("/missing-orders.xlsx")
def missing_orders_xlsx(up_to_month: Optional[str] = None, supplier: Optional[str] = None,
                        db: Session = Depends(get_db),
                        _: User = Depends(require_role("admin", "operator"))):
    data = fss.missing_orders_xlsx_bytes(db, up_to_month=up_to_month, supplier=supplier or DEFAULT_WOOD_SUPPLIER)
    fname = f"factory_missing_orders_{up_to_month or 'all'}.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


@router.get("/aliases")
def aliases(supplier: Optional[str] = None, db: Session = Depends(get_db),
            _: User = Depends(require_role("admin", "operator"))):
    return fss.list_aliases(db, supplier)


@router.post("/aliases")
def add_alias(payload: AliasIn, db: Session = Depends(get_db),
              _: User = Depends(require_role("admin", "operator"))):
    if not (payload.alias or "").strip():
        raise HTTPException(400, "别名不能为空")
    res = fss.add_alias(db, supplier=payload.supplier or DEFAULT_WOOD_SUPPLIER,
                        alias=payload.alias, note=payload.note)
    db.commit()
    return res


@router.delete("/aliases/{alias_id}")
def delete_alias(alias_id: int, db: Session = Depends(get_db),
                 _: User = Depends(require_role("admin", "operator"))):
    ok = fss.delete_alias(db, alias_id)
    if not ok:
        raise HTTPException(404, "别名不存在")
    db.commit()
    return {"deleted": True}
