"""客户聚合 + LTV 分级 (Phase 9, Tier 2 #5).

从订单表反向聚合, 写入 / 更新 Customer 主表.
分级:
    bronze    LTV < 5000
    silver    5000-20000
    gold      20000-50000
    platinum  > 50000

每天 06:30 跑一次 (`daily_06_customer_aggregate` 调度任务).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.customer import Customer
from app.models.marketing import AfterSales
from app.models.order import Order

_logger = logging.getLogger("panse.customer")


def _normalize_phone(p: Optional[str]) -> str:
    if not p:
        return ""
    return re.sub(r"[^0-9]", "", p)


def _matching_key(name: Optional[str], phone: Optional[str]) -> str:
    n = (name or "").strip()
    p = _normalize_phone(phone)
    return f"{p}|{n[:16]}"


def _tier_for(ltv: Decimal) -> str:
    if ltv >= 50000:
        return "platinum"
    if ltv >= 20000:
        return "gold"
    if ltv >= 5000:
        return "silver"
    return "bronze"


def aggregate_all(db: Session) -> dict:
    """全量重算所有客户. 适用于初始化或周期重计算."""
    orders = db.execute(
        select(Order).where(
            # 导入订单多为历史 + 中文状态; 收录所有真实成交客户, 仅排除关闭/取消
            Order.status.notin_(("cancelled",)),
            ~Order.status.like("%关闭%"),
            ~Order.status.like("%取消%"),
        )
    ).scalars().all()
    by_key: dict[str, dict] = {}
    for o in orders:
        name = (o.customer_name or "").strip()
        phone = _normalize_phone(o.customer_phone)
        # 业务要求: 只收录"同时有真实姓名和电话"的客户; 未解密/空的一律跳过
        if not name or not phone:
            continue
        key = _matching_key(o.customer_name, o.customer_phone)
        d = by_key.setdefault(key, {
            "name": o.customer_name or "",
            "phone": _normalize_phone(o.customer_phone),
            "address": o.customer_address,
            "first_order_at": o.order_date,
            "last_order_at": o.order_date,
            "total_orders": 0,
            "total_revenue": Decimal("0"),
            "total_returns": 0,
        })
        d["total_orders"] += 1
        d["total_revenue"] += Decimal(o.paid_amount or 0)
        if o.order_date and (d["first_order_at"] is None or o.order_date < d["first_order_at"]):
            d["first_order_at"] = o.order_date
        if o.order_date and (d["last_order_at"] is None or o.order_date > d["last_order_at"]):
            d["last_order_at"] = o.order_date
        if o.status == "aftersales":
            d["total_returns"] += 1

    # upsert
    upserted = 0
    for key, info in by_key.items():
        existing = db.execute(
            select(Customer).where(Customer.matching_key == key)
        ).scalar_one_or_none()
        if existing is None:
            c = Customer(
                matching_key=key, name=info["name"], phone=info["phone"],
                address=info["address"],
            )
            db.add(c)
        else:
            c = existing
            c.name = info["name"]
            c.phone = info["phone"]
            c.address = info["address"]
        c.total_orders = info["total_orders"]
        c.total_revenue = info["total_revenue"]
        c.total_returns = info["total_returns"]
        c.first_order_at = (
            datetime.combine(info["first_order_at"], datetime.min.time()).replace(tzinfo=timezone.utc)
            if info["first_order_at"] else None
        )
        c.last_order_at = (
            datetime.combine(info["last_order_at"], datetime.min.time()).replace(tzinfo=timezone.utc)
            if info["last_order_at"] else None
        )
        c.tier = _tier_for(c.total_revenue)
        upserted += 1
    db.flush()
    return {"customer_count": upserted, "tiers": _tier_counts(db)}


def _tier_counts(db: Session) -> dict:
    rows = db.execute(select(Customer)).scalars().all()
    counts = {"bronze": 0, "silver": 0, "gold": 0, "platinum": 0}
    for c in rows:
        counts[c.tier or "bronze"] = counts.get(c.tier or "bronze", 0) + 1
    return counts


def find_for_order(db: Session, order: Order) -> Optional[Customer]:
    key = _matching_key(order.customer_name, order.customer_phone)
    return db.execute(
        select(Customer).where(Customer.matching_key == key)
    ).scalar_one_or_none()
