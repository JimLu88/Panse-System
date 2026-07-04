"""物料价格按生效日版本化 (用户 2026-07-03)。

成本计算 (order_cost_service BOM 路径) 按订单 order_date 取"当时生效物料价" →
改物料价【前】的订单用旧价、改价【后】的订单用新价。

- material_price_at(code, on_date): 取 effective_from<=on_date 的最新价; 无历史回退当前 Material.price。
- record_change(code, new_price): 改价时记一行(生效日默认今天); 与最新同价则跳过(幂等)。
- seed_baseline(): 每有价物料建一行基线(=当前价, effective_from=基线日), 仅当该物料无历史时 →
  保证存量订单取到当前价(成本零变化), 且未来改价能按日期正确分档。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.material import Material, MaterialPriceHistory

# 种子基线日: 早于所有历史订单(最早 2026-01) → 存量订单一律取到"当前价"基线, 成本零变化。
SEED_BASELINE = date(2025, 1, 1)


def material_price_at(db: Session, material_code: Optional[str], on_date: date) -> Optional[Decimal]:
    """物料在 on_date 当时生效的价。取 effective_from<=on_date 的最新一行; 无历史回退当前 Material.price。"""
    if not material_code:
        return None
    row = db.execute(
        select(MaterialPriceHistory.price)
        .where(
            MaterialPriceHistory.material_code == material_code,
            MaterialPriceHistory.effective_from <= on_date,
        )
        .order_by(MaterialPriceHistory.effective_from.desc(), MaterialPriceHistory.id.desc())
        .limit(1)
    ).scalar()
    if row is not None:
        return Decimal(str(row))
    cur = db.execute(select(Material.price).where(Material.code == material_code)).scalar()
    return Decimal(str(cur)) if cur is not None else None


def _latest_price(db: Session, material_code: str) -> Optional[Decimal]:
    row = db.execute(
        select(MaterialPriceHistory.price)
        .where(MaterialPriceHistory.material_code == material_code)
        .order_by(MaterialPriceHistory.effective_from.desc(), MaterialPriceHistory.id.desc())
        .limit(1)
    ).scalar()
    return Decimal(str(row)) if row is not None else None


def record_change(db: Session, material_code: str, new_price, *,
                  effective_from: Optional[date] = None, note: Optional[str] = None) -> bool:
    """改价记一行(生效日默认今天)。与最新一行同价 → 跳过(幂等)。返回是否新增。"""
    if new_price is None or not material_code:
        return False
    new_price = Decimal(str(new_price))
    if _latest_price(db, material_code) == new_price:
        return False
    db.add(MaterialPriceHistory(
        material_code=material_code, price=new_price,
        effective_from=effective_from or date.today(), note=note or "改价",
    ))
    db.flush()
    return True


def seed_baseline(db: Session, baseline: date = SEED_BASELINE) -> dict:
    """每有价物料建一行基线(=当前价, effective_from=baseline), 仅当该物料无任何历史时。"""
    existing = {c for (c,) in db.execute(
        select(MaterialPriceHistory.material_code).distinct()).all()}
    seeded = 0
    for code, price in db.execute(select(Material.code, Material.price)).all():
        if code in existing or price is None:
            continue
        db.add(MaterialPriceHistory(
            material_code=code, price=Decimal(str(price)),
            effective_from=baseline, note="种子基线(当前价)",
        ))
        seeded += 1
    if seeded:
        db.flush()
    return {"seeded": seeded, "baseline": baseline.isoformat()}
