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


_CENTS = Decimal("0.01")


def _d(v) -> Optional[Decimal]:
    return Decimal(str(v)) if v is not None else None


def _median(vals: list[Decimal]) -> Optional[Decimal]:
    """兄弟 SKU 费用的中位数 (稳健代表值; 同产品不同尺寸费用有差异时取中间)。"""
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    return s[n // 2] if n % 2 else ((s[n // 2 - 1] + s[n // 2]) / 2)


def _pick(ps, field: str, base_map: dict, base: Optional[str]):
    """精确 SKU 有该费用值优先; 否则按基础产品码兄弟 SKU 中位数。"""
    v = getattr(ps, field, None) if ps else None
    if v is not None:
        return _d(v)
    return base_map.get(base) if base else None


def estimate_fee(sku_code, by_sku: dict, base_maps: dict, fallback_base: Optional[str] = None):
    """该 SKU 的 (预估打包, 预估物流, 预估安装) 单价:
       精确 SKU 有值优先; 否则(定制尾号≥90 / 定价表缺该 SKU)按 **基础产品码** 兄弟 SKU 中位数
       (用户 2026-06-21: 定制按产品编码取费, 一般相同)。任何口径都可调本函数。

       fallback_base (用户 2026-06-28): sku_code 为空/解析不出 base 时, 用订单 product_code 兜底
       取该产品兄弟 SKU 中位数 —— 否则 sku_code=None 的单只能落全局中位数, 与 theoretical(按
       product_code 命中定价表)口径不一致, 导致 physical_cost swap 基线错、有实际账单时多算。"""
    ps = by_sku.get(sku_code) if sku_code else None
    base = sku_utils.base_product_code(sku_code) or fallback_base
    return (_pick(ps, "packaging_cost", base_maps["pk"], base),
            _pick(ps, "logistics_cost", base_maps["lg"], base),
            _pick(ps, "install_cost", base_maps["inst"], base))


def _unit_physical(sku_code, by_sku: dict, base_maps: dict):
    """该 SKU 单件物理成本(定价表): 精确SKU优先, 否则基础产品码中位数。供 _effective_qty 判真多件用。"""
    return _pick(by_sku.get(sku_code) if sku_code else None, "physical_cost",
                 base_maps.get("phys", {}), sku_utils.base_product_code(sku_code))


def _build_price_maps(db: Session):
    rows = db.execute(select(
        PricingSku.sku_code, PricingSku.packaging_cost,
        PricingSku.logistics_cost, PricingSku.install_cost,
        PricingSku.physical_cost)).all()
    by_sku = {p.sku_code: p for p in rows}
    lists = {"pk": {}, "lg": {}, "inst": {}, "phys": {}}
    fields = {"pk": "packaging_cost", "lg": "logistics_cost", "inst": "install_cost",
              "phys": "physical_cost"}
    for p in rows:
        base = sku_utils.base_product_code(p.sku_code)
        if not base:
            continue
        for k, fld in fields.items():
            v = getattr(p, fld)
            if v is not None:
                lists[k].setdefault(base, []).append(_d(v))
    base_maps = {k: {b: _median(v) for b, v in d.items()} for k, d in lists.items()}
    return by_sku, base_maps


def sync_fee_components(db: Session, *, order_nos: Optional[list[str]] = None) -> dict:
    """回填订单的 est_packing/est_logistics(定价表×qty) + actual_packing/actual_logistics(账单Σ)。
    order_nos=None → 全部订单。返回 {est_set, actual_packing_set, actual_logistics_set}。"""
    # ---- 预估(打包/物流/安装): 精确SKU优先, 定制/缺失按基础产品码兄弟中位数, 都无则系统比例×实付, × qty ----
    by_sku, base_maps = _build_price_maps(db)
    o_stmt = select(Order)
    if order_nos:
        o_stmt = o_stmt.where(Order.order_no.in_(order_nos))
    orders = db.execute(o_stmt).scalars().all()
    from app.services.order_cost_service import _effective_qty
    from app.models.order import OrderDetail
    KS = ("pk", "lg", "inst")
    # 多商品单(≥2导入产品行): fee 按各行汇总(行 sku × 行 qty), 与 theoretical 的 _multi_product_cost 同口径。
    # 否则只取主 SKU → 副商品的打包/物流没进 est, 打包修复时只减了主SKU嵌入打包→多商品单仍残留副商品打包重复(用户 2026-06-26)。
    lines_by_order: dict[str, list] = {}
    for ln in db.execute(select(
            OrderDetail.order_no, OrderDetail.sku_code, OrderDetail.qty
        ).where(OrderDetail.source == "import")).all():
        lines_by_order.setdefault(ln.order_no, []).append(ln)
    est: dict[str, dict] = {}
    for o in orders:
        lns = lines_by_order.get(o.order_no, [])
        if len(lns) >= 2:
            agg = {k: Decimal("0") for k in KS}
            ok = {k: False for k in KS}
            for ln in lns:
                u = dict(zip(KS, estimate_fee(ln.sku_code, by_sku, base_maps, fallback_base=o.product_code)))
                q = int(ln.qty or 1)
                for k in KS:
                    if u[k] is not None:
                        agg[k] += u[k] * q
                        ok[k] = True
            est[o.order_no] = {k: (agg[k] if ok[k] else None) for k in KS}
        else:
            units = dict(zip(KS, estimate_fee(o.sku_code, by_sku, base_maps, fallback_base=o.product_code)))
            # 真实计价件数: 与 theoretical_cost 同口径(_effective_qty) —— 定制单 / 凑价单(件均实付<单件成本)
            # 按 1 件算, 否则 qty。修(用户 2026-06-25): 原来 ×原始qty 会把固定费用×10(定制凑价单qty=10)
            # 估成垃圾(餐桌物流估成¥5000), 现按真实件数乘。
            eff_qty = _effective_qty(o, _unit_physical(o.sku_code, by_sku, base_maps))
            est[o.order_no] = {k: (units[k] * eff_qty) if units[k] is not None else None for k in KS}
    # 兜底(estimate_fee 取不到定价表费用时): 用 全局中位数(定价表该费用)。
    # 修(用户 2026-06-25): 原"比例×实付"会把固定费用随实付放大成垃圾(餐桌物流估成¥5000) —
    # 打包/物流/安装是按件大致固定的费用, 不随订单金额线性放大, 故改用所有有值单的中位数兜底。
    med = {}
    for k in KS:
        vals = sorted(v for v in (est[o.order_no][k] for o in orders) if v is not None)
        med[k] = vals[len(vals) // 2] if vals else None
    est_set = 0
    for o in orders:
        new = {}
        for k in KS:
            v = est[o.order_no][k]
            if v is None:
                v = med[k]   # 全局中位数兜底(固定费用, 不随实付放大)
            # 量化到分(列是 Numeric(12,2)): 中位数会算出 3 位小数(如 336.665)→ 存库被 DB
            # 截成 336.67 → 下次又算 336.665 != 336.67 → 永久翻动。量化后 sync 才幂等(用户 2026-06-28)。
            new[k] = v.quantize(_CENTS) if v is not None else None
        if (o.est_packing != new["pk"] or o.est_logistics != new["lg"]
                or o.est_install != new["inst"]):
            o.est_packing, o.est_logistics, o.est_install = new["pk"], new["lg"], new["inst"]
            est_set += 1

    # ---- 实际: 打包账单Σ / 德邦逐单Σ / 安装=订单 install_fee+upstairs_fee(已在订单上) ----
    pk_sum: dict[str, Decimal] = {}
    for no, fee in db.execute(select(PackingBill.matched_order_no, PackingBill.packing_fee).where(
            PackingBill.matched_order_no.isnot(None), PackingBill.excluded == False)).all():  # noqa: E712
        if fee is not None:
            pk_sum[no] = pk_sum.get(no, Decimal("0")) + _d(fee)
    lg_sum: dict[str, Decimal] = {}
    for no, fee in db.execute(select(LogisticsBill.order_no, LogisticsBill.freight_amount).where(
            LogisticsBill.order_no.isnot(None), LogisticsBill.row_type == "line")).all():
        if fee is not None:
            lg_sum[no] = lg_sum.get(no, Decimal("0")) + _d(fee)

    ap_set = al_set = ai_set = 0
    for o in orders:
        new_ap = pk_sum.get(o.order_no)
        new_al = lg_sum.get(o.order_no)
        if_, uf = _d(o.install_fee), _d(o.upstairs_fee)
        new_ai = ((if_ or Decimal("0")) + (uf or Decimal("0"))) if (if_ is not None or uf is not None) else None
        if o.actual_packing != new_ap:
            o.actual_packing = new_ap
            ap_set += 1
        if o.actual_logistics != new_al:
            o.actual_logistics = new_al
            al_set += 1
        if o.actual_install != new_ai:
            o.actual_install = new_ai
            ai_set += 1
    db.flush()
    return {"est_set": est_set, "actual_packing_set": ap_set, "actual_logistics_set": al_set,
            "actual_install_set": ai_set, "orders_scanned": len(orders)}
