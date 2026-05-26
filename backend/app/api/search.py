"""全局搜索 (Phase 10, Tier 3 #14, 借鉴 Stripe).

GET /api/search?q=xxx

跨模型查: Order / Customer / Material / Product / Supplier / AfterSales / Alert.
返回统一格式 {kind, id, title, subtitle, url}.

简单 LIKE 实现 (生产可换 PG `tsvector` 或 elasticsearch).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_role
from app.models.alert import Alert
from app.models.auth import User
from app.models.customer import Customer
from app.models.marketing import AfterSales
from app.models.material import Material
from app.models.order import Order
from app.models.product import Product
from app.models.supplier import Supplier

router = APIRouter(prefix="/api/search", tags=["search"])


class SearchHit(BaseModel):
    kind: str          # order / customer / material / product / supplier / aftersales / alert
    id: int
    title: str
    subtitle: Optional[str] = None
    url: str


@router.get("", response_model=list[SearchHit])
def search(
    q: str = Query(..., min_length=1, max_length=64),
    limit: int = 50,
    db: Session = Depends(get_db),
    _: User = Depends(require_role("admin", "operator", "viewer")),
):
    """业务: 输入任意关键字 (订单号 / 电话 / 物料名 / 客户名) 全局找."""
    if not q.strip():
        return []
    pattern = f"%{q.strip()}%"
    out: list[SearchHit] = []

    # Orders
    for o in db.execute(
        select(Order).where(
            or_(Order.order_no.ilike(pattern), Order.customer_name.ilike(pattern),
                Order.customer_phone.ilike(pattern), Order.product_name.ilike(pattern)),
        ).limit(15)
    ).scalars():
        out.append(SearchHit(
            kind="order", id=o.id,
            title=f"订单 {o.order_no}",
            subtitle=f"{o.customer_name or '?'} · {o.product_name or '?'} · {o.status}",
            url=f"/orders?q={o.order_no}",
        ))

    # Customers
    for c in db.execute(
        select(Customer).where(
            or_(Customer.name.ilike(pattern), Customer.phone.ilike(pattern)),
        ).limit(10)
    ).scalars():
        out.append(SearchHit(
            kind="customer", id=c.id,
            title=f"客户 {c.name}",
            subtitle=f"{c.phone or '?'} · {c.tier} · {c.total_orders}单·¥{c.total_revenue}",
            url=f"/customers?id={c.id}",
        ))

    # Materials
    for m in db.execute(
        select(Material).where(
            or_(Material.code.ilike(pattern), Material.name.ilike(pattern)),
        ).limit(10)
    ).scalars():
        out.append(SearchHit(
            kind="material", id=m.id,
            title=f"物料 {m.code}",
            subtitle=f"{m.name} · 优先级 {m.priority}",
            url=f"/inventory?code={m.code}",
        ))

    # Products
    for p in db.execute(
        select(Product).where(
            or_(Product.code.ilike(pattern), Product.name.ilike(pattern)),
        ).limit(10)
    ).scalars():
        out.append(SearchHit(
            kind="product", id=p.id,
            title=f"产品 {p.code}",
            subtitle=p.name,
            url=f"/products?code={p.code}",
        ))

    # Suppliers
    for s in db.execute(
        select(Supplier).where(Supplier.name.ilike(pattern)).limit(5)
    ).scalars():
        out.append(SearchHit(
            kind="supplier", id=s.id, title=f"供应商 {s.name}",
            subtitle=s.supplier_type,
            url=f"/suppliers?id={s.id}",
        ))

    # AfterSales
    for a in db.execute(
        select(AfterSales).where(AfterSales.platform_order_no.ilike(pattern)).limit(5)
    ).scalars():
        out.append(SearchHit(
            kind="aftersales", id=a.id,
            title=f"售后 #{a.id} - {a.platform_order_no}",
            subtitle=a.reason or "",
            url="/aftersales",
        ))

    # Alerts
    for al in db.execute(
        select(Alert).where(
            Alert.resolved_at.is_(None),
            or_(Alert.title.ilike(pattern), Alert.body.ilike(pattern)),
        ).limit(5)
    ).scalars():
        out.append(SearchHit(
            kind="alert", id=al.id, title=al.title,
            subtitle=al.kind, url=al.related_url or "/",
        ))

    return out[:limit]
