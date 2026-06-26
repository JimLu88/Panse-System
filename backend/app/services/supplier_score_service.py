"""供应商月度评分卡 — 6 维全自动 (用户 2026-06-27: 全部从现有数据算, 不手填才更准)。

每月 1 号 09:00 调度器跑一次, 算上月数据 → 写 SupplierScore (新维度存 detail_json, 免迁移)。

评分维度 (子分 0-1, 加权 ×100):
    on_time          按时率   —— 采购到货(purchase_date)≤ 关联订单发货日(ship_date) 的比例。
                                  ⚠真要准得记「应到货日」, 现以"赶在发货前到"为代理。
    quality(退货/问题) 退货率   —— 争议(disputed)送货单占比 (真正"供应商缺陷退货"系统无干净关联, 先以争议代理)。
    price_competitiveness 价格竞争力 —— 这家同料单价 vs 全体均价(同行对标), 越便宜越高 (按金额加权)。
    recon_consistency 对账一致性 —— 采购/送货能对上系统订单的比例(可追溯到订单 = 账实), 按金额加权。
    price_stability   价格稳定 —— 单价环比波动越小越高。
采购规模/依赖度 (采购额/占比/单一来源) 作【风险上下文】展示, 不计入质量分。

综合分 = Σ(权重 × 可得子分) / Σ(可得权重) × 100 (缺数据的维度不拖分, 只用算得出的)。
数据: PartPurchase(supplier 名匹配) + DeliveryNote(supplier_id) + Order(ship_date/est_parts)。
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.order import Order, PartPurchase
from app.models.supplier import DeliveryNote, DeliveryNoteLine, Supplier
from app.models.supplier_score import SupplierScore

_logger = logging.getLogger("panse.supplier_score")

# 质量分权重 (采购规模/依赖度不计入分, 作风险上下文)
_WEIGHTS = {
    "on_time": Decimal("0.25"),
    "quality": Decimal("0.20"),               # = 1 - 退货率
    "price_competitiveness": Decimal("0.25"),
    "recon_consistency": Decimal("0.20"),
    "price_stability": Decimal("0.10"),
}

# 非配件采购(代扣/理财/服务费…)排除关键词 — 与 parts_recon 一致
_NON_PART_KW = ("代扣", "代付", "资金扣回", "消费券", "理财", "申购",
                "服务费", "手续费", "余额宝", "转入", "转出", "单次转", "转账")


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal("0")


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    end = (date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)) - timedelta(days=1)
    return start, end


def _is_part(p: PartPurchase) -> bool:
    name = p.material_name or ""
    if any(k in name for k in _NON_PART_KW):
        return False
    if p.supplier and "淘天" in p.supplier:
        return False
    return True


def _mat_key(p: PartPurchase) -> Optional[str]:
    """同料对标的键: 优先料号, 否则物料名去空白。"""
    if p.material_code:
        return p.material_code.strip()
    if p.material_name:
        return "".join(p.material_name.split())
    return None


def _avg_unit_price(purchases: list) -> Decimal:
    tq = ta = Decimal("0")
    for p in purchases:
        qty = _d(p.qty)
        if p.unit_price is not None and qty > 0:
            tq += qty
            ta += qty * _d(p.unit_price)
        elif p.amount is not None and qty > 0:
            tq += qty
            ta += _d(p.amount)
    return (ta / tq) if tq > 0 else Decimal("0")


def _purch_amount(p: PartPurchase) -> Decimal:
    return _d(p.total_amount if p.total_amount is not None else p.amount)


def compute_for_month(db: Session, year: int, month: int) -> list[SupplierScore]:
    """算指定月所有活跃供应商的 6 维评分 (全自动)。"""
    start, end = _month_bounds(year, month)
    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    prev_start, prev_end = _month_bounds(prev_year, prev_month)

    # ── 当月全体配件采购 (对标/规模/单一来源 用) ──
    all_purch = [p for p in db.execute(
        select(PartPurchase).where(
            PartPurchase.purchase_date >= start, PartPurchase.purchase_date <= end)
    ).scalars().all() if _is_part(p)]
    by_supplier: dict[str, list] = defaultdict(list)
    for p in all_purch:
        if p.supplier:
            by_supplier[p.supplier].append(p)
    # 同料全体均价(对标基准) + 该料的供货商集合(单一来源)
    mat_all: dict[str, list] = defaultdict(list)
    mat_suppliers: dict[str, set] = defaultdict(set)
    for p in all_purch:
        k = _mat_key(p)
        if k:
            mat_all[k].append(p)
            if p.supplier:
                mat_suppliers[k].add(p.supplier)
    peer_avg = {k: _avg_unit_price(ps) for k, ps in mat_all.items()}
    grand_total = sum((_purch_amount(p) for p in all_purch), Decimal("0"))

    # 关联订单 → (发货日, 配件标准估值) 查表
    onos = {(p.related_order_no or "").strip() for p in all_purch if p.related_order_no}
    order_info: dict[str, tuple] = {}
    onos = {x for x in onos if x}
    if onos:
        for o in db.execute(select(Order).where(Order.order_no.in_(list(onos)))).scalars().all():
            order_info[o.order_no] = (o.ship_date, o.est_parts)

    suppliers = db.execute(select(Supplier).where(Supplier.is_active == True)).scalars().all()  # noqa: E712
    out: list[SupplierScore] = []
    for sup in suppliers:
        purchases = by_supplier.get(sup.name, [])
        prev_purchases = [p for p in db.execute(
            select(PartPurchase).where(
                PartPurchase.supplier == sup.name,
                PartPurchase.purchase_date >= prev_start, PartPurchase.purchase_date <= prev_end)
        ).scalars().all() if _is_part(p)]
        notes = db.execute(
            select(DeliveryNote).where(
                DeliveryNote.supplier_id == sup.id,
                DeliveryNote.delivery_date >= start, DeliveryNote.delivery_date <= end)
        ).scalars().all()
        if not purchases and not notes:
            continue

        total_amount = sum((_purch_amount(p) for p in purchases), Decimal("0"))
        subs: dict[str, Decimal] = {}   # 维度 → 子分 0-1 (只放算得出的)
        detail: dict = {}

        # 1) 按时率: 采购到货 ≤ 订单发货日 (代理)
        assessable = ontime = 0
        for p in purchases:
            ono = (p.related_order_no or "").strip()
            info = order_info.get(ono)
            if info and info[0] and p.purchase_date:
                assessable += 1
                if p.purchase_date <= info[0]:
                    ontime += 1
        if assessable:
            r = Decimal(ontime) / Decimal(assessable)
            subs["on_time"] = r
            detail["on_time"] = {"rate": float(r), "assessable": assessable, "basis": "代理(到货≤发货日)"}
        else:
            detail["on_time"] = {"rate": None, "assessable": 0, "basis": "无可评估(缺应到货日)"}

        # 2) 退货/问题率: 争议送货单占比 (代理)
        if notes:
            disputed = sum(1 for n in notes if n.status == "disputed")
            rr = Decimal(disputed) / Decimal(len(notes))
            subs["quality"] = Decimal("1") - rr
            detail["return"] = {"rate": float(rr), "disputed": disputed, "notes": len(notes), "basis": "代理(争议单)"}
        else:
            detail["return"] = {"rate": None, "notes": 0, "basis": "本月无送货单"}

        # 3) 价格波动 (稳定子分)
        cur_avg, prev_avg = _avg_unit_price(purchases), _avg_unit_price(prev_purchases)
        if prev_avg > 0:
            var = (cur_avg - prev_avg) / prev_avg * Decimal("100")
            stab = Decimal("1") - min(abs(var) / Decimal("50"), Decimal("1"))
            subs["price_stability"] = stab
            detail["price_variance_pct"] = float(var)
        else:
            detail["price_variance_pct"] = None
            var = Decimal("0")

        # 4) 价格竞争力: 同料 vs 全体均价 (越便宜越高, 按金额加权)
        comp_w = comp_s = Decimal("0")
        for p in purchases:
            k = _mat_key(p)
            if not k or peer_avg.get(k, Decimal("0")) <= 0:
                continue
            if len(mat_suppliers.get(k, set())) < 2:
                continue   # 只此一家供的料无从比价(对标自己永远=1, 无意义)
            up = _d(p.unit_price) if p.unit_price is not None else None
            if up is None or up <= 0:
                q = _d(p.qty)
                up = (_purch_amount(p) / q) if q > 0 else None   # 没单价用 金额/数量 兜底
            if up is None or up <= 0:
                continue
            ratio = up / peer_avg[k]
            s = max(Decimal("0"), min(Decimal("1"), Decimal("2") - ratio))  # 同行价→1, 贵20%→0.8
            w = _purch_amount(p)
            comp_w += w
            comp_s += s * w
        if comp_w > 0:
            cs = comp_s / comp_w
            subs["price_competitiveness"] = cs
            detail["price_competitiveness"] = {"score": float(cs), "basis": "同料对标全体均价(金额加权)"}
        else:
            detail["price_competitiveness"] = {"score": None, "basis": "无同料可对标"}

        # 5) 对账一致性: 能追溯到订单的金额占比 (账实)
        traced = sum((_purch_amount(p) for p in purchases if (p.related_order_no or "").strip()), Decimal("0"))
        note_ids = [n.id for n in notes]
        if note_ids:
            lines = db.execute(select(DeliveryNoteLine).where(DeliveryNoteLine.delivery_note_id.in_(note_ids))).scalars().all()
            ln_total = sum((_d(l.amount) for l in lines), Decimal("0"))
            ln_matched = sum((_d(l.amount) for l in lines if l.matched_order_no), Decimal("0"))
        else:
            ln_total = ln_matched = Decimal("0")
        denom = total_amount + ln_total
        matched_amt = traced + ln_matched
        if denom > 0 and (traced > 0 or ln_total > 0):
            rc = matched_amt / denom
            subs["recon_consistency"] = rc
            detail["recon_consistency"] = {"matched_rate": float(rc), "basis": "可追溯订单金额占比"}
        else:
            # 纯货款流水(无订单关联、无送货单)→ 无对账基础, 记"数据不足"而非 0(避免误导性 0 分)
            detail["recon_consistency"] = {"matched_rate": None, "basis": "无订单关联/送货单可对账"}

        # 6) 采购规模/依赖度 (风险上下文, 不计分)
        share = (total_amount / grand_total) if grand_total > 0 else Decimal("0")
        single_src = sorted({k for p in purchases if (k := _mat_key(p)) and len(mat_suppliers.get(k, set())) == 1})
        detail["scale"] = {
            "total_amount": float(total_amount), "share_pct": float((share * 100).quantize(Decimal("0.01"))),
            "single_source_materials": single_src[:20], "single_source_count": len(single_src),
        }

        # ── 综合分 (只用算得出的维度, 缺的不拖分) ──
        avail_w = sum((_WEIGHTS[k] for k in subs), Decimal("0"))
        if avail_w > 0:
            score = (sum((_WEIGHTS[k] * subs[k] for k in subs), Decimal("0")) / avail_w * Decimal("100")).quantize(Decimal("0.01"))
        else:
            score = None
        detail["weights_used"] = {k: float(_WEIGHTS[k]) for k in subs}
        detail["dims_real"] = sorted(subs.keys())

        # upsert
        existing = db.execute(select(SupplierScore).where(
            SupplierScore.supplier_id == sup.id, SupplierScore.year == year, SupplierScore.month == month
        )).scalar_one_or_none()
        on_time_col = subs.get("on_time")
        return_col = (Decimal("1") - subs["quality"]) if "quality" in subs else None
        if existing is None:
            s = SupplierScore(
                supplier_id=sup.id, year=year, month=month,
                on_time_rate=on_time_col, return_rate=return_col,
                price_variance_pct=var if prev_avg > 0 else None,
                total_orders=len(purchases), total_amount=total_amount,
                score=score, detail_json=detail)
            db.add(s)
        else:
            s = existing
            s.on_time_rate = on_time_col
            s.return_rate = return_col
            s.price_variance_pct = var if prev_avg > 0 else None
            s.total_orders = len(purchases)
            s.total_amount = total_amount
            s.score = score
            s.detail_json = detail
        out.append(s)
    db.flush()

    out.sort(key=lambda x: (x.score if x.score is not None else Decimal("-1")), reverse=True)
    for i, s in enumerate(out, start=1):
        s.rank = i
    db.flush()
    return out


def list_for_month(db: Session, year: int, month: int) -> list[SupplierScore]:
    return list(db.execute(
        select(SupplierScore).where(
            SupplierScore.year == year, SupplierScore.month == month,
        ).order_by(SupplierScore.rank.asc().nulls_last())
    ).scalars())


def history_for_supplier(db: Session, supplier_id: int, *, limit: int = 12) -> list[SupplierScore]:
    """某供应商最近 N 个月的评分(新→旧), 给详情页评分卡 + 趋势用。"""
    return list(db.execute(
        select(SupplierScore).where(SupplierScore.supplier_id == supplier_id)
        .order_by(SupplierScore.year.desc(), SupplierScore.month.desc()).limit(limit)
    ).scalars())
