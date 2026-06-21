# -*- coding: utf-8 -*-
"""订单 预估/实际 打包+物流费 分量回填 — 实际账单覆盖预估 (用户 2026-06-21)。

- 预估(est): 定价表 PricingSku.packaging_cost / logistics_cost × qty (与 theoretical_cost 同口径:
  theoretical = ps.physical_cost × qty, 各分量同样 × qty)。
- 实际(actual): 精确配到逐单账单 — 打包账单 Σ packing_fee(未剔除) / 德邦逐单 Σ freight(row_type=line)。
physical_cost 据此把"配到的分量"从预估换成实际(只换配到的, 未配/月结汇总保持预估)。

不跑 recompute_and_save(定制单铁律), 只读定价表 + 账单, 直接 UPDATE 订单的 4 个分量列。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.finance import LogisticsBill, PackingBill
from app.models.order import Order
from app.models.pricing import PricingSku


def _d(v) -> Optional[Decimal]:
    return Decimal(str(v)) if v is not None else None


def sync_fee_components(db: Session, *, order_nos: Optional[list[str]] = None) -> dict:
    """回填订单的 est_packing/est_logistics(定价表×qty) + actual_packing/actual_logistics(账单Σ)。
    order_nos=None → 全部订单。返回 {est_set, actual_packing_set, actual_logistics_set}。"""
    # ---- 预估: 定价表按 sku_code × qty ----
    price = {p.sku_code: p for p in db.execute(
        select(PricingSku.sku_code, PricingSku.packaging_cost, PricingSku.logistics_cost)
    ).all()}
    o_stmt = select(Order)
    if order_nos:
        o_stmt = o_stmt.where(Order.order_no.in_(order_nos))
    orders = db.execute(o_stmt).scalars().all()
    est_set = 0
    for o in orders:
        ps = price.get(o.sku_code) if o.sku_code else None
        qty = int(o.qty or 1)
        new_pk = (_d(ps.packaging_cost) * qty) if (ps and ps.packaging_cost is not None) else None
        new_lg = (_d(ps.logistics_cost) * qty) if (ps and ps.logistics_cost is not None) else None
        if o.est_packing != new_pk or o.est_logistics != new_lg:
            o.est_packing, o.est_logistics = new_pk, new_lg
            est_set += 1

    # ---- 实际: 账单按订单 Σ ----
    pk_stmt = select(PackingBill.matched_order_no,
                     PackingBill.packing_fee).where(
        PackingBill.matched_order_no.isnot(None), PackingBill.excluded == False)  # noqa: E712
    pk_sum: dict[str, Decimal] = {}
    for no, fee in db.execute(pk_stmt).all():
        if fee is not None:
            pk_sum[no] = pk_sum.get(no, Decimal("0")) + _d(fee)
    lg_stmt = select(LogisticsBill.order_no, LogisticsBill.freight_amount).where(
        LogisticsBill.order_no.isnot(None), LogisticsBill.row_type == "line")
    lg_sum: dict[str, Decimal] = {}
    for no, fee in db.execute(lg_stmt).all():
        if fee is not None:
            lg_sum[no] = lg_sum.get(no, Decimal("0")) + _d(fee)

    ap_set = al_set = 0
    for o in orders:
        new_ap = pk_sum.get(o.order_no)
        new_al = lg_sum.get(o.order_no)
        if o.actual_packing != new_ap:
            o.actual_packing = new_ap
            ap_set += 1
        if o.actual_logistics != new_al:
            o.actual_logistics = new_al
            al_set += 1
    db.flush()
    return {"est_set": est_set, "actual_packing_set": ap_set, "actual_logistics_set": al_set,
            "orders_scanned": len(orders)}
