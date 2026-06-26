# -*- coding: utf-8 -*-
"""配件(外采)对账服务 — 配件 epic P2 (用户 2026-06-26)。

两件事:
  A. aggregate_related_purchases —— 把「能逐单」的配件采购单(PartPurchase.related_order_no 填了订单号)
     按订单号汇总, 写进 Order.actual_parts → 该单 physical_cost 改逐项真实计价(不估不 floor)。
     默认 dry-run(apply=False)只出预览(含 physical_cost 变化), 确认无误再 apply。

  B. bulk_material_recon —— 大宗/消耗型材料(洞石板/木皮/双面胶/螺丝…)工厂混裁、说不清对应哪单,
     无法逐单。改按「材料 × 采购周期」对账: 当期实际采购额 vs 标准估值消耗, 出差异%。
     **⚠ 铁律(用户 2026-06-26): 消费窗口一律按订单「发货日期 ship_date」圈定, 不用下单日期。**
     因生产周期~30天: 某段时间采购的料被那段时间【发货】的订单消耗。差异喂 P3 逐单建议值回推。

口径: 总账准(差异落当期); 逐单这块是有界/可校准的近似(物理限制绕不开)。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.order import Order, PartPurchase, PartsMonthlyRecon
from app.services import sales_analytics

_CENTS = Decimal("0.01")


# ── 大宗/消耗材料登记表 ────────────────────────────────────────────────────
# 每条: key/name 显示; mode 决定「标准消耗」怎么算; purchase_kw 匹配采购侧(配件名/规格/供应商)。
#   mode='by_order_kw'  : 标准 = Σ est_parts of 命中 order_kw 的成交单(发货在窗口内)。
#                         适用「选配型」材料(只有部分订单选, 且其外采配件就是这个料, 如洞石/岩板饰面板)。
#   mode='per_order_flat': 标准 = flat_per_order × 当期发货成交单数。
#                         适用「通用消耗型」(几乎每单都用, 订单侧无关键词, 如双面胶/螺丝/木皮)。
#                         flat_per_order = 每单标准用量(¥); 设 0 → 仅显示实际采购, 待用户填标准后比对。
# 关键词可按实际发票/SKU 命名增删(模块常量, 改后重部署生效)。
BULK_MATERIALS: list[dict] = [
    {
        "key": "dongshi",
        "name": "洞石/岩板饰面板",
        "mode": "by_order_kw",
        "purchase_kw": ["洞石", "岩板", "饰面板", "饰面", "台面板"],
        "order_kw": ["洞石", "岩板"],
    },
    {
        "key": "muphi",
        "name": "木皮饰面",
        "mode": "per_order_flat",
        "purchase_kw": ["木皮"],
        "flat_per_order": Decimal("0"),
    },
    {
        "key": "tape",
        "name": "双面胶",
        "mode": "per_order_flat",
        "purchase_kw": ["双面胶"],
        "flat_per_order": Decimal("0"),
    },
    {
        "key": "screw",
        "name": "螺丝",
        "mode": "per_order_flat",
        "purchase_kw": ["螺丝"],
        "flat_per_order": Decimal("0"),
    },
]

# 非配件采购(代扣/理财/服务费/淘天扣款…)排除关键词 — 与 purchases.list_purchases 一致。
_NON_PART_KW = ("代扣", "代付", "资金扣回", "消费券", "理财", "申购",
                "服务费", "手续费", "余额宝", "转入", "转出", "单次转", "转账")


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal("0")


def _ym(d: Optional[date]) -> Optional[str]:
    return f"{d.year:04d}-{d.month:02d}" if d else None


def _looks_non_part(p: PartPurchase) -> bool:
    """该采购行像非配件支出(代扣/服务费/淘天)→ 不计入配件对账。"""
    name = (p.material_name or "")
    if any(k in name for k in _NON_PART_KW):
        return True
    if p.supplier and "淘天" in p.supplier:
        return True
    return False


def _purchase_amount(p: PartPurchase) -> Optional[Decimal]:
    """采购金额取数: total_amount(含运费总额)优先, 否则 amount(明细金额)。"""
    if p.total_amount is not None:
        return _d(p.total_amount)
    if p.amount is not None:
        return _d(p.amount)
    return None


def _match_kw(text: Optional[str], kws: list[str]) -> bool:
    if not text:
        return False
    return any(k in text for k in kws)


# ── A. 逐单配件采购 → actual_parts 汇总 ──────────────────────────────────────
def aggregate_related_purchases(db: Session, *, apply: bool = False) -> dict:
    """填了 related_order_no 的配件采购单 → 按订单号汇总写 Order.actual_parts。

    apply=False(默认): 只算预览, 不落库; 返回每单 physical_cost 变化供人工核对。
    apply=True: 写 actual_parts 并 commit(该单 physical_cost 转「逐项真实计价」)。
    """
    from app.services.order_financials import physical_cost

    rows = db.execute(
        select(PartPurchase).where(
            PartPurchase.related_order_no.isnot(None),
            PartPurchase.related_order_no != "",
        )
    ).scalars().all()

    by_order: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    counts: dict[str, int] = defaultdict(int)
    for p in rows:
        if _looks_non_part(p):
            continue
        amt = _purchase_amount(p)
        if amt is None or amt <= 0:
            continue
        ono = (p.related_order_no or "").strip()
        if not ono:
            continue
        by_order[ono] += amt
        counts[ono] += 1

    items: list[dict] = []
    applied = 0
    for ono, total in sorted(by_order.items()):
        total = total.quantize(_CENTS)
        o = db.execute(select(Order).where(Order.order_no == ono)).scalar_one_or_none()
        if o is None:
            items.append({"order_no": ono, "matched": False, "purchases": counts[ono],
                          "parts_total": float(total)})
            continue
        old_parts = o.actual_parts
        old_phys = physical_cost(o)
        o.actual_parts = total          # 临时置入算新成本
        new_phys = physical_cost(o)
        if apply:
            applied += 1
        else:
            o.actual_parts = old_parts  # dry-run 还原
        items.append({
            "order_no": ono, "matched": True, "purchases": counts[ono],
            "product_name": o.product_name, "is_custom": bool(o.is_custom),
            "old_actual_parts": float(_d(old_parts)) if old_parts is not None else None,
            "new_actual_parts": float(total),
            "old_physical_cost": float(old_phys), "new_physical_cost": float(new_phys),
            "physical_delta": float((new_phys - old_phys).quantize(_CENTS)),
        })

    if apply:
        db.commit()

    matched = [i for i in items if i["matched"]]
    return {
        "applied": apply,
        "applied_count": applied,
        "matched_orders": len(matched),
        "unmatched_orders": len(items) - len(matched),
        "total_parts_amount": float(sum((_d(i.get("new_actual_parts")) for i in matched), Decimal("0"))),
        "items": items,
    }


# ── B. 大宗材料 × 采购周期 对账 (发货日期口径) ───────────────────────────────
def _settled_shipped_orders(db: Session) -> list[Order]:
    """成交 + 已发货(ship_date 非空) + 非补单 的订单 (大宗对账的消费窗口基底, 按发货日期)。"""
    return db.execute(
        select(Order).where(
            sales_analytics.settled_sale_clause(),
            Order.is_refill.is_(False),
            Order.ship_date.isnot(None),
        )
    ).scalars().all()


def _material_for_key(material_key: str) -> Optional[dict]:
    for m in BULK_MATERIALS:
        if m["key"] == material_key:
            return m
    return None


def _material_name(material_key: str) -> Optional[str]:
    m = _material_for_key(material_key)
    return m["name"] if m else None


def _material_bom_kw(mat: dict) -> list[str]:
    """该材料在 BOM/订单侧的匹配关键词: by_order_kw 用 order_kw, 否则用 purchase_kw。"""
    return mat.get("order_kw") or mat.get("purchase_kw") or []


def _factory_actual_by_period(db: Session, material_key: str) -> dict[str, Decimal]:
    """该材料每月「工厂月度对账总额」(多供应商求和)。"""
    out: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for ym, amt in db.execute(
        select(PartsMonthlyRecon.year_month, PartsMonthlyRecon.actual_total)
        .where(PartsMonthlyRecon.material_key == material_key)
    ).all():
        out[ym] += _d(amt)
    return out


def bulk_material_recon(db: Session, *, granularity: str = "month") -> dict:
    """大宗/消耗材料对账: 每材料每月 历史平均 | 预估 | 实际(工厂月度对账) | 差异%。

    **消费窗口按订单 ship_date(发货日期)圈定** —— 生产周期~30天, 料在发货前才裁切消耗。
    - 预估 standard_consume = Σ est_parts of 命中该料的发货成交单(by_order_kw)/ flat×单数(per_order_flat)。
    - 实际 factory_actual = 该月工厂返回的对账总额(PartsMonthlyRecon 多供应商求和; 没录入=None)。
    - 历史平均 historical_avg = 过去已对账月份「每单实际」均值 × 本月发货单数(无历史→回退预估)。
    - 采购发票 purchase_invoice = PartPurchase 命中该料的当月发票合计(参考; 大宗多走支付宝常为空)。
    granularity 暂支持 'month'(YYYY-MM)。
    """
    purchases = db.execute(select(PartPurchase)).scalars().all()
    orders = _settled_shipped_orders(db)

    materials_out: list[dict] = []
    for mat in BULK_MATERIALS:
        pkw = mat["purchase_kw"]
        # 实际采购: 按采购日期(purchase_date)分月
        actual_by_p: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        for p in purchases:
            if _looks_non_part(p):
                continue
            if not (_match_kw(p.material_name, pkw) or _match_kw(p.spec, pkw)
                    or _match_kw(p.supplier, pkw)):
                continue
            amt = _purchase_amount(p)
            ym = _ym(p.purchase_date)
            if amt is None or ym is None:
                continue
            actual_by_p[ym] += amt

        # 标准消耗: 按发货日期(ship_date)分月
        std_by_p: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        cnt_by_p: dict[str, int] = defaultdict(int)
        miss_by_p: dict[str, int] = defaultdict(int)   # 命中但 est_parts 缺(覆盖率)
        mode = mat["mode"]
        for o in orders:
            ym = _ym(o.ship_date)
            if ym is None:
                continue
            if mode == "by_order_kw":
                okw = mat["order_kw"]
                if not (_match_kw(o.sku, okw) or _match_kw(o.product_name, okw)
                        or _match_kw(o.sku_code, okw)):
                    continue
                cnt_by_p[ym] += 1
                if o.est_parts is None:
                    miss_by_p[ym] += 1
                else:
                    std_by_p[ym] += _d(o.est_parts)
            else:  # per_order_flat: 每单标准用量 × 当期发货成交单数
                cnt_by_p[ym] += 1
                std_by_p[ym] += _d(mat.get("flat_per_order"))

        fact_by_p = _factory_actual_by_period(db, mat["key"])
        periods = sorted(set(actual_by_p) | set(std_by_p) | set(cnt_by_p) | set(fact_by_p))
        rows = []
        t_std = t_fact = t_invoice = Decimal("0")
        hist_rates: list[Decimal] = []   # 过去已对账月份的「每单实际」均价, 滚动喂历史平均
        for ym in periods:
            std = std_by_p.get(ym, Decimal("0")).quantize(_CENTS)
            invoice = actual_by_p.get(ym, Decimal("0")).quantize(_CENTS)   # 采购发票合计(参考)
            cnt = cnt_by_p.get(ym, 0)
            has_fact = ym in fact_by_p
            fact = fact_by_p.get(ym, Decimal("0")).quantize(_CENTS) if has_fact else None
            # 历史平均 = 过去已对账月份「每单实际」均值 × 本月发货单数; 无历史 → 回退预估
            if hist_rates and cnt > 0:
                avg_rate = sum(hist_rates, Decimal("0")) / Decimal(len(hist_rates))
                hist = (avg_rate * cnt).quantize(_CENTS)
            else:
                hist = std
            var = (fact - std).quantize(_CENTS) if has_fact else None
            var_pct = float((var / std * 100).quantize(_CENTS)) if (has_fact and std > 0) else None
            rows.append({
                "period": ym,
                "historical_avg": float(hist),
                "standard_consume": float(std),
                "factory_actual": float(fact) if has_fact else None,
                "has_factory_actual": has_fact,
                "variance": float(var) if has_fact else None,
                "variance_pct": var_pct,
                "purchase_invoice": float(invoice),
                "order_count": cnt,
                "missing_est": miss_by_p.get(ym, 0),
            })
            t_std += std
            t_invoice += invoice
            if has_fact:
                t_fact += fact
                if cnt > 0:
                    hist_rates.append(fact / Decimal(cnt))
        t_var = (t_fact - t_std).quantize(_CENTS)
        materials_out.append({
            "key": mat["key"],
            "name": mat["name"],
            "mode": mode,
            "periods": rows,
            "total_standard": float(t_std),
            "total_factory_actual": float(t_fact),
            "total_purchase_invoice": float(t_invoice),
            "total_variance": float(t_var),
            "total_variance_pct": float((t_var / t_std * 100).quantize(_CENTS)) if t_std > 0 else None,
        })

    return {"granularity": granularity, "ship_date_basis": True, "materials": materials_out}


# ── C. 工厂月度对账总额 录入/查询/删除 ──────────────────────────────────────
def list_monthly_recon(db: Session, *, material_key: Optional[str] = None,
                       year_month: Optional[str] = None) -> list[dict]:
    q = select(PartsMonthlyRecon)
    if material_key:
        q = q.where(PartsMonthlyRecon.material_key == material_key)
    if year_month:
        q = q.where(PartsMonthlyRecon.year_month == year_month)
    rows = db.execute(
        q.order_by(PartsMonthlyRecon.year_month.desc(), PartsMonthlyRecon.id.desc())
    ).scalars().all()
    return [{
        "id": r.id, "material_key": r.material_key, "material_name": _material_name(r.material_key),
        "year_month": r.year_month, "supplier": r.supplier,
        "actual_total": float(_d(r.actual_total)), "note": r.note,
    } for r in rows]


def save_monthly_recon(db: Session, *, material_key: str, year_month: str, actual_total,
                       supplier: Optional[str] = None, note: Optional[str] = None,
                       recon_id: Optional[int] = None) -> dict:
    """录入/更新 工厂月度对账总额。recon_id 给了=更新该行, 否则新增一行(同料同月可多供应商)。"""
    if _material_for_key(material_key) is None:
        raise ValueError(f"未知材料 {material_key}")
    amt = _d(actual_total)
    if recon_id is not None:
        row = db.get(PartsMonthlyRecon, recon_id)
        if row is None:
            raise ValueError(f"对账记录 {recon_id} 不存在")
        row.material_key, row.year_month = material_key, year_month
        row.supplier, row.actual_total, row.note = (supplier or None), amt, (note or None)
    else:
        row = PartsMonthlyRecon(material_key=material_key, year_month=year_month,
                                supplier=supplier or None, actual_total=amt, note=note or None)
        db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id, "material_key": row.material_key, "material_name": _material_name(row.material_key),
            "year_month": row.year_month, "supplier": row.supplier,
            "actual_total": float(_d(row.actual_total)), "note": row.note}


def delete_monthly_recon(db: Session, recon_id: int) -> bool:
    row = db.get(PartsMonthlyRecon, recon_id)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


# ── D. 当月「已发货」订单清单导出 (发给工厂对账) ──────────────────────────────
def _orders_shipped_in(db: Session, year_month: str) -> list[Order]:
    return [o for o in _settled_shipped_orders(db) if _ym(o.ship_date) == year_month]


def export_shipped_orders(db: Session, *, year_month: str,
                          material_key: Optional[str] = None) -> dict:
    """导出当月(发货月)已发货成交订单清单, 发给工厂对账。

    material_key 空 = 全部发货单(基础列, 给工厂自己挑); 给了 = 只列用该材料的发货单, 且逐单展开
    BOM 部位/预设尺寸明细(实际可能有出入, 列出方便对照)。按发货日期口径(ship_date)。
    """
    orders = _orders_shipped_in(db, year_month)
    mat = _material_for_key(material_key) if material_key else None
    out_orders: list[dict] = []
    t_est = Decimal("0")

    if mat is not None:
        from app.services.accessory_checklist_service import _bom_rows_for_order
        bom_kw = _material_bom_kw(mat)
        okw = mat.get("order_kw") or bom_kw
        for o in orders:
            hit_by_name = bool(_match_kw(o.sku, okw) or _match_kw(o.product_name, okw)
                               or _match_kw(o.sku_code, okw))
            bom_parts = []
            for line, mat_name, mat_unit in _bom_rows_for_order(db, o):
                nm = mat_name or line.material_name or ""
                if not (_match_kw(nm, bom_kw) or _match_kw(line.material_code, bom_kw)):
                    continue
                qty = _d(line.qty_per_product) * Decimal(int(o.qty or 1))
                bom_parts.append({
                    "part_name": nm,
                    "material_code": line.material_code,
                    "qty": float(qty),
                    "unit": line.unit or mat_unit,
                    "size_note": (line.remark or "").strip() or None,   # 预设尺寸/工艺说明
                })
            # 选配型(by_order_kw): 名字命中 或 BOM 有该料才算; 通用消耗型: BOM 里确实有该料才列
            if mat["mode"] == "by_order_kw":
                if not hit_by_name and not bom_parts:
                    continue
            elif not bom_parts:
                continue
            est = _d(o.est_parts)
            t_est += est
            out_orders.append({
                "order_no": o.order_no,
                "ship_date": o.ship_date.isoformat() if o.ship_date else None,
                "customer_name": o.customer_name,
                "product_name": o.product_name,
                "sku": o.sku,                       # SKU 名通常含整体尺寸
                "est_parts": float(est),
                "bom_parts": bom_parts,
            })
    else:
        for o in orders:
            est = _d(o.est_parts)
            t_est += est
            out_orders.append({
                "order_no": o.order_no,
                "ship_date": o.ship_date.isoformat() if o.ship_date else None,
                "customer_name": o.customer_name,
                "product_name": o.product_name,
                "sku": o.sku,
                "est_parts": float(est),
            })

    out_orders.sort(key=lambda x: (x["ship_date"] or "", x["order_no"]))
    return {
        "year_month": year_month,
        "material_key": material_key,
        "material_name": mat["name"] if mat else None,
        "order_count": len(out_orders),
        "total_est_parts": float(t_est.quantize(_CENTS)),
        "orders": out_orders,
    }
