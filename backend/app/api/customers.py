"""客户 CRM API (Phase 9 Tier 2 #5)."""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.models.auth import User
from app.models.customer import Customer
from app.models.order import Order
from app.services import customer_service

router = APIRouter(prefix="/api/customers", tags=["customers"])


class CustomerOut(BaseModel):
    id: int
    name: str
    phone: Optional[str]
    address: Optional[str]
    tier: str
    first_order_at: Optional[str]
    last_order_at: Optional[str]
    total_orders: int
    total_revenue: float
    total_returns: int
    tags: Optional[list]
    note: Optional[str]


def _out(c: Customer) -> CustomerOut:
    return CustomerOut(
        id=c.id, name=c.name, phone=c.phone, address=c.address, tier=c.tier,
        first_order_at=c.first_order_at.isoformat() if c.first_order_at else None,
        last_order_at=c.last_order_at.isoformat() if c.last_order_at else None,
        total_orders=c.total_orders,
        total_revenue=float(c.total_revenue or Decimal("0")),
        total_returns=c.total_returns,
        tags=c.tags or [], note=c.note,
    )


@router.get("", response_model=list[CustomerOut])
def list_customers(
    q: Optional[str] = None,
    tier: Optional[str] = None,
    limit: int = Query(50, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    stmt = select(Customer).order_by(Customer.total_revenue.desc())
    if q:
        from sqlalchemy import or_
        stmt = stmt.where(or_(
            Customer.name.ilike(f"%{q}%"),
            Customer.phone.ilike(f"%{q}%"),
        ))
    if tier:
        stmt = stmt.where(Customer.tier == tier)
    stmt = stmt.limit(limit)
    return [_out(c) for c in db.execute(stmt).scalars()]


@router.get("/{customer_id}", response_model=CustomerOut)
def get_customer(
    customer_id: int, db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    c = db.get(Customer, customer_id)
    if not c:
        raise HTTPException(404, "客户不存在")
    return _out(c)


@router.get("/{customer_id}/orders")
def get_customer_orders(
    customer_id: int, db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    c = db.get(Customer, customer_id)
    if not c:
        raise HTTPException(404, "客户不存在")
    rows = db.execute(
        select(Order).where(
            Order.customer_phone == c.phone,
        ).order_by(Order.id.desc()).limit(100)
    ).scalars().all()
    return [{
        "id": o.id, "order_no": o.order_no, "order_date": o.order_date.isoformat() if o.order_date else None,
        "status": o.status, "qty": o.qty,
        "paid_amount": float(o.paid_amount or 0),
        "product_name": o.product_name,
    } for o in rows]


@router.post("/aggregate")
def trigger_aggregate(
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin")),
):
    """admin 手动触发: 重算所有客户聚合."""
    r = customer_service.aggregate_all(db)
    db.commit()
    return r
