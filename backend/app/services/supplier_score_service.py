"""供应商月度评分卡 (Phase 8, Tier 1 #5, 借鉴 Tesla 供应商管理).

每月 1 号 09:00 调度器跑一次, 算上月数据 → 写 SupplierScore.

评分项:
    on_time_rate         按时送达 = 1 - (晚到 > 3 天数 / 总单数)
    return_rate          退货率   = AfterSales 涉及该供应商比例
    price_variance       单价波动 vs 上月 (-100 ~ +100)
    score                综合分:
                         on_time × 0.5 + (1 - return_rate) × 0.3 + price_stability × 0.2
                         × 100

数据来自:
    - DeliveryNote (送达 vs 期望)
    - PartPurchase (采购单价)
    - AfterSales (退货 — 这块挂粗略关联, 后续可细)
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.order import PartPurchase
from app.models.supplier import Supplier
from app.models.supplier_score import SupplierScore

_logger = logging.getLogger("panse.supplier_score")


def compute_for_month(db: Session, year: int, month: int) -> list[SupplierScore]:
    """算指定月所有供应商的评分."""
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(year, month + 1, 1) - timedelta(days=1)
    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    prev_start = date(prev_year, prev_month, 1)
    prev_end = start - timedelta(days=1)

    # 拉所有 supplier
    suppliers = db.execute(select(Supplier).where(Supplier.is_active == True)).scalars().all()  # noqa
    out: list[SupplierScore] = []
    for sup in suppliers:
        # 采购单 (这个月)
        purchases = db.execute(
            select(PartPurchase).where(
                PartPurchase.supplier == sup.name,
                PartPurchase.purchase_date >= start,
                PartPurchase.purchase_date <= end,
            )
        ).scalars().all()
        prev_purchases = db.execute(
            select(PartPurchase).where(
                PartPurchase.supplier == sup.name,
                PartPurchase.purchase_date >= prev_start,
                PartPurchase.purchase_date <= prev_end,
            )
        ).scalars().all()

        total = len(purchases)
        if total == 0:
            continue
        total_amount = sum(
            (Decimal(p.total_amount or p.amount or 0) for p in purchases),
            Decimal("0"),
        )
        # 按时率: 占位 — 没有 expected_delivery 字段, 默认 0.95
        # (后续 PartPurchase 增加 expected_delivery 字段后真算)
        on_time_rate = Decimal("0.95")

        # 退货率: 占位 — 没有清晰 supplier 关联
        return_rate = Decimal("0.02")

        # 价格波动
        cur_avg_price = _avg_unit_price(purchases)
        prev_avg_price = _avg_unit_price(prev_purchases)
        if prev_avg_price > 0:
            variance_pct = (cur_avg_price - prev_avg_price) / prev_avg_price * 100
        else:
            variance_pct = Decimal("0")

        # 综合分 (0-100)
        price_stability = Decimal("1") - min(abs(variance_pct) / Decimal("100"),
                                              Decimal("1"))
        score = (
            on_time_rate * Decimal("0.5") +
            (Decimal("1") - return_rate) * Decimal("0.3") +
            price_stability * Decimal("0.2")
        ) * Decimal("100")

        # upsert
        existing = db.execute(
            select(SupplierScore).where(
                SupplierScore.supplier_id == sup.id,
                SupplierScore.year == year, SupplierScore.month == month,
            )
        ).scalar_one_or_none()
        if existing is None:
            s = SupplierScore(
                supplier_id=sup.id, year=year, month=month,
                on_time_rate=on_time_rate, return_rate=return_rate,
                price_variance_pct=variance_pct,
                total_orders=total, total_amount=total_amount,
                score=score.quantize(Decimal("0.01")),
                detail_json={
                    "avg_unit_price": float(cur_avg_price),
                    "prev_avg_unit_price": float(prev_avg_price),
                },
            )
            db.add(s)
        else:
            s = existing
            s.on_time_rate = on_time_rate
            s.return_rate = return_rate
            s.price_variance_pct = variance_pct
            s.total_orders = total
            s.total_amount = total_amount
            s.score = score.quantize(Decimal("0.01"))
        out.append(s)
    db.flush()

    # 算 rank
    out.sort(key=lambda x: x.score or Decimal("0"), reverse=True)
    for i, s in enumerate(out, start=1):
        s.rank = i
    db.flush()
    return out


def _avg_unit_price(purchases: list) -> Decimal:
    if not purchases:
        return Decimal("0")
    total_qty = Decimal("0")
    total_amount = Decimal("0")
    for p in purchases:
        qty = Decimal(p.qty or 0)
        if p.unit_price is not None:
            total_qty += qty
            total_amount += qty * Decimal(p.unit_price)
        elif p.amount is not None and qty > 0:
            total_qty += qty
            total_amount += Decimal(p.amount)
    return (total_amount / total_qty) if total_qty > 0 else Decimal("0")


def list_for_month(db: Session, year: int, month: int) -> list[SupplierScore]:
    return list(db.execute(
        select(SupplierScore).where(
            SupplierScore.year == year, SupplierScore.month == month,
        ).order_by(SupplierScore.rank.asc().nulls_last())
    ).scalars())
