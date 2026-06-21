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
from app.services import sku_utils


def _d(v) -> Optional[Decimal]:
    return Decimal(str(v)) if v is not None else None


def _median(vals: list[Decimal]) -> Optional[Decimal]:
    """兄弟 SKU 费用的中位数 (稳健代表值; 同产品不同尺寸费用有差异时取中间)。"""
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    return s[n // 2] if n % 2 else ((s[n // 2 - 1] + s[n // 2]) / 2)


def estimate_fee(sku_code, by_sku: dict, base_pk: dict, base_lg: dict):
    """该 SKU 的 (预估打包, 预估物流) 单价:
       精确 SKU 有值优先; 否则(定制尾号≥90 / 定价表缺该 SKU)按 **基础产品码** 兄弟 SKU 中位数
       (用户 2026-06-21: 定制按产品编码取费, 一般相同)。任何口径都可调本函数。"""
    ps = by_sku.get(sku_code) if sku_code else None
    base = sku_utils.base_product_code(sku_code)
    pk = _d(ps.packaging_cost) if (ps and ps.packaging_cost is not None) else (base_pk.get(base) if base else None)
    lg = _d(ps.logistics_cost) if (ps and ps.logistics_cost is not None) else (base_lg.get(base) if base else None)
    return pk, lg


def _build_price_maps(db: Session):
    rows = db.execute(select(
        PricingSku.sku_code, PricingSku.packaging_cost, PricingSku.logistics_cost)).all()
    by_sku = {p.sku_code: p for p in rows}
    pk_lists: dict[str, list] = {}
    lg_lists: dict[str, list] = {}
    for p in rows:
        base = sku_utils.base_product_code(p.sku_code)
        if not base:
            continue
        if p.packaging_cost is not None:
            pk_lists.setdefault(base, []).append(_d(p.packaging_cost))
        if p.logistics_cost is not None:
            lg_lists.setdefault(base, []).append(_d(p.logistics_cost))
    base_pk = {b: _median(v) for b, v in pk_lists.items()}
    base_lg = {b: _median(v) for b, v in lg_lists.items()}
    return by_sku, base_pk, base_lg


def sync_fee_components(db: Session, *, order_nos: Optional[list[str]] = None) -> dict:
    """回填订单的 est_packing/est_logistics(定价表×qty) + actual_packing/actual_logistics(账单Σ)。
    order_nos=None → 全部订单。返回 {est_set, actual_packing_set, actual_logistics_set}。"""
    # ---- 预估: 精确 SKU 优先, 定制/缺失按基础产品码兄弟中位数, × qty ----
    by_sku, base_pk, base_lg = _build_price_maps(db)
    o_stmt = select(Order)
    if order_nos:
        o_stmt = o_stmt.where(Order.order_no.in_(order_nos))
    orders = db.execute(o_stmt).scalars().all()
    est_set = 0
    for o in orders:
        pk, lg = estimate_fee(o.sku_code, by_sku, base_pk, base_lg)
        qty = int(o.qty or 1)
        new_pk = (pk * qty) if pk is not None else None
        new_lg = (lg * qty) if lg is not None else None
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
