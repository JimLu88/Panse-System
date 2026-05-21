"""默认供应商种子 (业务需求: 木作 / 岩板 / 玻璃 三家先有).

idempotent: 同名供应商已存在则跳过。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.supplier import Supplier


DEFAULT_SUPPLIERS = (
    {"name": "木作工厂", "supplier_type": "woodwork",
     "payment_terms": "月结", "remark": "柜体板件主力供应商"},
    {"name": "岩板厂", "supplier_type": "rock_slab",
     "payment_terms": "月结", "remark": "大理石/岩板桌面"},
    {"name": "玻璃厂", "supplier_type": "glass",
     "payment_terms": "月结", "remark": "钢化玻璃柜门 / 隔板"},
)


def seed_default_suppliers(db: Session) -> list[Supplier]:
    created: list[Supplier] = []
    for spec in DEFAULT_SUPPLIERS:
        existing = db.execute(
            select(Supplier).where(Supplier.name == spec["name"])
        ).scalar_one_or_none()
        if existing is not None:
            continue
        s = Supplier(**spec)
        db.add(s)
        created.append(s)
    if created:
        db.flush()
    return created
