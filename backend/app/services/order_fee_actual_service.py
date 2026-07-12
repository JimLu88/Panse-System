# -*- coding: utf-8 -*-
"""订单 预估/实际 打包+物流费 分量回填 — 实际账单覆盖预估 (用户 2026-06-21)。

- 预估(est): 定价表 PricingSku.packaging_cost / logistics_cost × qty (与 theoretical_cost 同口径:
  theoretical = ps.physical_cost × qty, 各分量同样 × qty)。
- 实际(actual): 精确配到逐单账单 — 打包账单 Σ packing_fee(未剔除) / 德邦逐单 Σ freight(row_type=line)。
physical_cost 据此把"配到的分量"从预估换成实际(只换配到的, 未配/月结汇总保持预估)。

不跑 recompute_and_save(定制单铁律), 只读定价表 + 账单, 直接 UPDATE 订单的 4 个分量列。

写入语义 (2026-07-11 修"清空雷", 用户拍板"只设不清"):
- 全量(order_nos=None, 账单导入/自动配单触发): 账单配到才写; **没配到的订单保留现值**。
  旧行为"全量对齐账单"(没账单→写 None)会把手工合并的实际费用整批抹掉(实测将抹
  物流¥10,438×124单 + 打包¥9,554×140单 + 安装¥697×65单, 1-4月成本虚高 +5,105),
  且每次导账单都重演 —— 已废除。
- 定点(order_nos 指定, 人工改配单/删账单行触发): 保留"对齐"语义(可清)——取消配单/删行
  就是用户"这单没这笔费用"的明确意图(打包删除端点注释: 删后回退该订单)。定点模式不再
  重刷 est_*(配单变化与预估无关; 且子集中位数兜底会把无定价单的 est 误清)。
- 安装(actual_install): 恒【只填空】—— 非空不覆盖不清(安装费不来自账单配对, 覆盖会踩掉
  手工合并的实际安装费)。
- 被覆盖/清空的旧值首次写入 system_settings["fee_sync_prev_values"] 备份, 可人工回滚。
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


_PREV_KEY = "fee_sync_prev_values"


def _stash_prev_values(db: Session, prev: dict) -> None:
    """被覆盖/清空的旧值首次备份进 system_settings[fee_sync_prev_values] (JSON, 每单每字段只存
    最早一份, 供人工回滚)。备份失败只跳过, 绝不阻断回填。"""
    try:
        import json
        from app.models.settings import SystemSetting
        row = db.execute(select(SystemSetting).where(SystemSetting.key == _PREV_KEY)).scalar_one_or_none()
        if row is None:
            row = SystemSetting(key=_PREV_KEY)
            db.add(row)
        try:
            data = json.loads(row.value_plain) if row.value_plain else {}
        except Exception:  # noqa: BLE001 — 备份键损坏则重建, 不阻断
            data = {}
        changed = False
        for no, fields in prev.items():
            slot = data.setdefault(no, {})
            for f, v in fields.items():
                if f not in slot:
                    slot[f] = v
                    changed = True
        if changed:
            row.value_plain = json.dumps(data, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        pass


def sync_fee_components(db: Session, *, order_nos: Optional[list[str]] = None,
                        clear_unmatched: Optional[bool] = None) -> dict:
    """回填订单的 est_packing/est_logistics(定价表×qty) + actual_packing/actual_logistics(账单Σ)。
    order_nos=None → 全部订单(只设不清, 见模块 docstring); 指定 → 定点(默认可清=对齐语义)。
    clear_unmatched 显式传入可覆盖默认。返回 {est_set, actual_packing_set, actual_logistics_set, ...}。"""
    if clear_unmatched is None:
        clear_unmatched = order_nos is not None   # 全量=只设不清; 定点(人工改配单)=对齐可清
    o_stmt = select(Order)
    if order_nos:
        o_stmt = o_stmt.where(Order.order_no.in_(order_nos))
    orders = db.execute(o_stmt).scalars().all()
    est_set = 0
    if order_nos is None:
        # ---- 预估(打包/物流/安装): 精确SKU优先, 定制/缺失按基础产品码兄弟中位数, 都无则全局中位, × qty ----
        # 仅全量模式跑: 定点(人工改配单)与预估无关, 且子集中位数兜底会把无定价单的 est 误清 (2026-07-11)。
        by_sku, base_maps = _build_price_maps(db)
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

    # 万师傅首装 → actual_install 兜底 (2026-07-12 用户: 安装账单也要同步到订单总表):
    # 订单自身 install_fee/upstairs_fee 都空时, 用万师傅逐单档案(已配对+交易成功+非维修)最早一单的净额
    # 作实际安装费。只取首装不含返修(与"核对表L=首装, 返修属售后"的 2026-07-11 裁定一致); 恒只填空。
    from app.models.finance import WanshifuOrder
    ws_first: dict[str, tuple] = {}    # order_no -> (created_time, net_amount)
    for w in db.execute(select(WanshifuOrder).where(
            WanshifuOrder.matched_order_no.isnot(None),
            WanshifuOrder.status == "交易成功")).scalars().all():
        if w.net_amount is None or "维修" in (w.service_type or ""):
            continue
        prev = ws_first.get(w.matched_order_no)
        key_t = w.created_time
        if prev is None or (key_t is not None and (prev[0] is None or key_t < prev[0])):
            ws_first[w.matched_order_no] = (key_t, Decimal(str(w.net_amount)))

    ap_set = al_set = ai_set = kept = 0
    prev_backup: dict = {}
    for o in orders:
        if_, uf = _d(o.install_fee), _d(o.upstairs_fee)
        new_ai = ((if_ or Decimal("0")) + (uf or Decimal("0"))) if (if_ is not None or uf is not None) else None
        if new_ai is None and o.order_no in ws_first:
            new_ai = ws_first[o.order_no][1]   # 万师傅首装净额兜底
        # 打包/物流: 账单配到才写; 没账单 → 全量保留现值(只设不清), 定点按对齐语义可清
        for attr, new in (("actual_packing", pk_sum.get(o.order_no)),
                          ("actual_logistics", lg_sum.get(o.order_no))):
            cur = getattr(o, attr)
            if new is None and not clear_unmatched:
                if cur is not None:
                    kept += 1        # 手工合并/历史实报, 无账单 → 保留
                continue
            if cur == new:
                continue
            if cur is not None:      # 覆盖/清空前把最早旧值备份进 settings, 可回滚
                prev_backup.setdefault(o.order_no, {}).setdefault(attr, str(cur))
            setattr(o, attr, new)
            if attr == "actual_packing":
                ap_set += 1
            else:
                al_set += 1
        # 安装: 恒只填空(非空不覆盖不清)——不来自账单配对, 覆盖会踩掉手工合并的实际安装费
        if new_ai is not None and o.actual_install is None:
            o.actual_install = new_ai
            ai_set += 1
    if prev_backup:
        _stash_prev_values(db, prev_backup)
    db.flush()
    return {"est_set": est_set, "actual_packing_set": ap_set, "actual_logistics_set": al_set,
            "actual_install_set": ai_set, "kept_manual": kept, "orders_scanned": len(orders)}
