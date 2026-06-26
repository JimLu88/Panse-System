# -*- coding: utf-8 -*-
"""配件(外采)对账服务 — 配件 epic (用户 2026-06-26; 方向1: 分类 + BOM 驱动)。

三件事:
  A. aggregate_related_purchases —— 填了 related_order_no 的配件采购单按订单汇总写 Order.actual_parts。
  B. bulk_material_recon —— **按 Material.category 分组、BOM 驱动** 的对账: 每分类每发货月
     历史平均 | 预估(Σ发货单BOM里该类外采配件 price×qty) | 实际(工厂月度对账总额) | 差异%。
     **铁律: 消费窗口按订单「发货日期 ship_date」圈定(生产周期~30天)。** 取代旧硬编码关键词登记表。
  C. 工厂月度对账总额录入(PartsMonthlyRecon, material_key 存「分类」)。
  D. export_shipped_orders —— 导当月发货单清单(全部 / 按分类逐单展开 BOM 部位+预设尺寸)给工厂对账。

口径: 总账准(差异落当期); 逐单这块是有界/可校准的近似(物理限制绕不开)。
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.material import Material
from app.models.order import Order, PartPurchase, PartsMonthlyRecon
from app.services import sales_analytics

_CENTS = Decimal("0.01")

# 工厂自备/木作/人工(WD-/MW-/MP-)不是外采配件 → 不进配件对账 (按料号前缀硬排, 名字会误命中)。
_FACTORY_PREFIXES = ("WD", "MW", "MP")
_UNCATEGORIZED = "未分类"

# 结算模式 (用户 2026-06-27): 五金都在五金店买 → 月结(月度总额对账, 工厂/供应商填);
# 其余(杂项/岩板/洞石饰面板/玻璃/大宗料…)= 零星采购(用了才买, 支付宝备注/导入逐项归账, 实际=Σ真实采购)。
# 月结类才"导清单给对方填总额"; 零星类没"对方", 实际从采购单(PartPurchase)来。
_MONTHLY_SETTLE_CATEGORIES = ("五金", "电力轨道", "岩板")   # 有固定供应商、按月对账的; 杂项/洞石饰面板/玻璃/木皮等=零星


def _settle_mode(category: str) -> str:
    return "月结" if category in _MONTHLY_SETTLE_CATEGORIES else "零星"

# 非配件采购(代扣/理财/服务费/淘天扣款…)排除关键词 — 与 purchases.list_purchases 一致。
_NON_PART_KW = ("代扣", "代付", "资金扣回", "消费券", "理财", "申购",
                "服务费", "手续费", "余额宝", "转入", "转出", "单次转", "转账")


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal("0")


def _ym(d: Optional[date]) -> Optional[str]:
    return f"{d.year:04d}-{d.month:02d}" if d else None


def _size_area(size: Optional[str]) -> Decimal:
    """从尺寸备注取前两个数相乘当"面积"(定制单同料合并时取最大者偏保守)。无数→0。"""
    if not size:
        return Decimal("0")
    nums = re.findall(r"\d+(?:\.\d+)?", size)
    if len(nums) >= 2:
        return Decimal(nums[0]) * Decimal(nums[1])
    return Decimal(nums[0]) if nums else Decimal("0")


def _looks_non_part(p: PartPurchase) -> bool:
    name = (p.material_name or "")
    if any(k in name for k in _NON_PART_KW):
        return True
    if p.supplier and "淘天" in p.supplier:
        return True
    return False


def _purchase_amount(p: PartPurchase) -> Optional[Decimal]:
    if p.total_amount is not None:
        return _d(p.total_amount)
    if p.amount is not None:
        return _d(p.amount)
    return None


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


# ── BOM 驱动的物料消耗 (取代硬编码关键词; 用户 2026-06-26 方向1) ──────────────
def _settled_shipped_orders(db: Session) -> list[Order]:
    """成交 + 已发货(ship_date 非空) + 非补单 的订单(对账消费窗口基底, 按发货日期)。"""
    return db.execute(
        select(Order).where(
            sales_analytics.settled_sale_clause(),
            Order.is_refill.is_(False),
            Order.ship_date.isnot(None),
        )
    ).scalars().all()


def _product_code_variants(code: Optional[str]) -> list[str]:
    """订单 product_code 用 P+11, BOM 用 PPS+11 → 两形态都试(镜像 accessory_checklist)。"""
    if not code:
        return []
    code = code.strip()
    out = {code}
    if code.startswith("PPS"):
        out.add("P" + code[3:])
    elif code.startswith("P"):
        out.add("PPS" + code[1:])
    return list(out)


def _load_bom_and_materials(db: Session):
    """一次性载入: 物料(category/price/name/unit) + BOM(按 (product_code,sku_code) 与 product_code 两键)。"""
    from app.models.bom import BomLine
    mat_info: dict[str, dict] = {}
    for code, name, unit, price, cat in db.execute(
        select(Material.code, Material.name, Material.unit, Material.price, Material.category)
    ).all():
        mat_info[code] = {"name": name, "unit": unit, "price": _d(price), "category": cat}
    bom_by_pcsku: dict[tuple, list] = defaultdict(list)
    bom_by_pc: dict[str, list] = defaultdict(list)
    for pc, sc, mcode, qty, remark, mname in db.execute(
        select(BomLine.product_code, BomLine.sku_code, BomLine.material_code,
               BomLine.qty_per_product, BomLine.remark, BomLine.material_name)
    ).all():
        rec = (mcode, _d(qty), remark, mname)
        bom_by_pcsku[(pc, sc)].append(rec)
        bom_by_pc[pc].append(rec)
    return mat_info, bom_by_pcsku, bom_by_pc


def _order_bom_lines(o: Order, bom_by_pcsku, bom_by_pc) -> list:
    """该订单的 BOM 行(镜像 _bom_rows_for_order: 先 product_code+sku_code 精确, 再 product_code 回退)。"""
    pcs = _product_code_variants(o.product_code)
    for pc in pcs:
        if (pc, o.sku_code) in bom_by_pcsku:
            return bom_by_pcsku[(pc, o.sku_code)]
    for pc in pcs:
        if pc in bom_by_pc:
            return bom_by_pc[pc]
    return []


def _order_is_custom(o: Order) -> bool:
    if bool(getattr(o, "is_custom", False)):
        return True
    from app.services import sku_utils
    try:
        return bool(sku_utils.is_custom_sku_code(o.sku_code, o.product_code))
    except Exception:  # noqa: BLE001
        return False


def _order_category_consumption(o: Order, mat_info, bom_by_pcsku, bom_by_pc) -> dict[str, dict]:
    """该订单按分类的外采配件消耗: {category: {amount, materials:[{...}]}}。排木作/工厂自备(WD/MW/MP);
    qty = qty_per_product × 订单件数。

    - 非定制: 同料同尺寸去重。
    - 定制单(用户 2026-06-27): BOM 是"模板"(同一种料堆了多个尺寸, 如洞洞板 1155/955/755), 实际一单只用
      一种 → **同料只取一行**(面积最大者, 成本偏保守); 模板有多个尺寸时标 `size_uncertain` 让清单提示
      人工确认尺寸。这同时修正了对账预估对定制单的"同料多算"。
    """
    qmul = Decimal(int(o.qty or 1))
    is_custom = _order_is_custom(o)
    by_cat: dict[str, dict] = {}
    seen: set = set()
    custom_pick: dict[str, dict] = {}   # 定制单: mcode -> 选中料(面积最大) + alt(被合并的其它尺寸数)

    def _emit(mcode, q, price, size, cat, name, info, alt=0):
        amt = (q * price).quantize(_CENTS)
        c = by_cat.setdefault(cat, {"amount": Decimal("0"), "materials": []})
        c["amount"] += amt
        m = {
            "material_code": mcode, "part_name": name, "category": cat,
            "qty": float(q), "unit": info.get("unit"), "price": float(price),
            "amount": float(amt), "size_note": size,
        }
        if alt > 0:
            m["size_uncertain"] = True
            m["alt_size_count"] = alt
        c["materials"].append(m)

    for mcode, qty, remark, mname in _order_bom_lines(o, bom_by_pcsku, bom_by_pc):
        if (mcode or "").split("-", 1)[0].upper() in _FACTORY_PREFIXES:
            continue
        info = mat_info.get(mcode) or {}
        cat = info.get("category") or _UNCATEGORIZED
        name = info.get("name") or mname or mcode
        size = (remark or "").strip() or None
        q = qty * qmul
        price = info.get("price", Decimal("0"))
        if is_custom:
            area = _size_area(size)
            prev = custom_pick.get(mcode)
            if prev is None:
                custom_pick[mcode] = {"q": q, "price": price, "size": size, "cat": cat,
                                      "name": name, "info": info, "area": area, "alt": 0}
            else:
                prev["alt"] += 1
                if area > prev["area"]:
                    custom_pick[mcode] = {"q": q, "price": price, "size": size, "cat": cat,
                                          "name": name, "info": info, "area": area, "alt": prev["alt"]}
            continue
        dk = (mcode, "".join(size.split()) if size else "")
        if dk in seen:
            continue
        seen.add(dk)
        _emit(mcode, q, price, size, cat, name, info)

    for mcode, p in custom_pick.items():
        _emit(mcode, p["q"], p["price"], p["size"], p["cat"], p["name"], p["info"], alt=p["alt"])
    return by_cat


def _ac_categories(db: Session) -> list[str]:
    """配件库里 AC(外采)物料用到的分类(去重, 排工厂自备前缀)。"""
    cats: set = set()
    for code, cat in db.execute(
        select(Material.code, Material.category).where(Material.category.isnot(None))
    ).all():
        if (code or "").split("-", 1)[0].upper() in _FACTORY_PREFIXES:
            continue
        if cat:
            cats.add(cat)
    return sorted(cats)


# ── B. 分类 × 发货月 对账 (BOM 驱动, 发货日期口径) ───────────────────────────
def bulk_material_recon(db: Session, *, granularity: str = "month") -> dict:
    """配件对账(方向1, 分类+BOM 驱动): 每分类每发货月 历史平均 | 预估 | 实际(工厂月度) | 差异%。

    - 预估 standard_consume = Σ(发货成交单 BOM 里该分类外采配件 price×qty)(按 ship_date 分月)。
    - 实际 factory_actual = 工厂月度对账总额(PartsMonthlyRecon, key=分类, 多供应商求和; 没录=None)。
    - 历史平均 historical_avg = 过去已对账月「每单实际」均值 × 本月发货单数(无历史→回退预估)。
    """
    mat_info, bom_by_pcsku, bom_by_pc = _load_bom_and_materials(db)
    orders = _settled_shipped_orders(db)

    std: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    cnt: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for o in orders:
        ym = _ym(o.ship_date)
        if ym is None:
            continue
        for cat, c in _order_category_consumption(o, mat_info, bom_by_pcsku, bom_by_pc).items():
            std[cat][ym] += c["amount"]
            cnt[cat][ym] += 1

    fact_all: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    for key, ym, amt in db.execute(
        select(PartsMonthlyRecon.material_key, PartsMonthlyRecon.year_month, PartsMonthlyRecon.actual_total)
    ).all():
        fact_all[key][ym] += _d(amt)

    # 零星类"实际" = 真实采购单(支付宝备注/导入)按分类×采购月汇总 (用户 2026-06-27)。
    # 经 PartPurchase.material_code → 配件库分类; 未编码的采购映射不到分类(诚实留空, 进未分类不强塞)。
    purch_all: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: Decimal("0")))
    for mcode, amt, pdate in db.execute(
        select(PartPurchase.material_code, PartPurchase.amount, PartPurchase.purchase_date)
        .where(PartPurchase.amount.isnot(None))
    ).all():
        cat = (mat_info.get(mcode) or {}).get("category") if mcode else None
        ym = _ym(pdate)
        if cat and ym and (mcode or "").split("-", 1)[0].upper() not in _FACTORY_PREFIXES:
            purch_all[cat][ym] += _d(abs(amt))

    out_cats = []
    all_keys = sorted(set(_ac_categories(db)) | set(std.keys()) | set(fact_all.keys()) | set(purch_all.keys()))
    for cat in all_keys:
        mode = _settle_mode(cat)   # 月结(五金/电力轨道) | 零星(其余)
        std_p, cnt_p = std.get(cat, {}), cnt.get(cat, {})
        fact_p, purch_p = fact_all.get(cat, {}), purch_all.get(cat, {})
        periods = sorted(set(std_p) | set(cnt_p) | set(fact_p) | set(purch_p))
        rows = []
        t_std = t_fact = t_purch = t_actual = Decimal("0")
        hist_rates: list[Decimal] = []
        for ym in periods:
            s = std_p.get(ym, Decimal("0")).quantize(_CENTS)
            c = cnt_p.get(ym, 0)
            has_fact = ym in fact_p
            fact = fact_p.get(ym, Decimal("0")).quantize(_CENTS) if has_fact else None
            has_purch = ym in purch_p
            purch = purch_p.get(ym, Decimal("0")).quantize(_CENTS) if has_purch else None
            # 月结类"实际"=工厂月度对账总额; 零星类"实际"=真实采购单(支付宝备注/导入)
            actual = fact if mode == "月结" else purch
            has_actual = has_fact if mode == "月结" else has_purch
            if hist_rates and c > 0:
                hist = ((sum(hist_rates, Decimal("0")) / Decimal(len(hist_rates))) * c).quantize(_CENTS)
            else:
                hist = s
            var = (actual - s).quantize(_CENTS) if (has_actual and actual is not None) else None
            var_pct = float((var / s * 100).quantize(_CENTS)) if (var is not None and s > 0) else None
            rows.append({
                "period": ym, "historical_avg": float(hist), "standard_consume": float(s),
                "factory_actual": float(fact) if has_fact else None, "has_factory_actual": has_fact,
                "actual_purchase": float(purch) if has_purch else None, "has_actual_purchase": has_purch,
                "settle_mode": mode,
                "actual": float(actual) if (has_actual and actual is not None) else None, "has_actual": has_actual,
                "variance": float(var) if var is not None else None, "variance_pct": var_pct,
                "order_count": c,
            })
            t_std += s
            if has_fact:
                t_fact += fact
            if has_purch:
                t_purch += purch
            if has_actual and actual is not None:
                t_actual += actual
                if c > 0:
                    hist_rates.append(actual / Decimal(c))
        t_var = (t_actual - t_std).quantize(_CENTS)
        out_cats.append({
            "key": cat, "name": cat, "settle_mode": mode, "periods": rows,
            "total_standard": float(t_std),
            "total_factory_actual": float(t_fact), "total_actual_purchase": float(t_purch),
            "total_actual": float(t_actual), "total_variance": float(t_var),
            "total_variance_pct": float((t_var / t_std * 100).quantize(_CENTS)) if t_std > 0 else None,
        })
    return {"granularity": granularity, "ship_date_basis": True, "category_driven": True, "materials": out_cats}


# ── C. 工厂月度对账总额 录入/查询/删除 (material_key 存「分类」) ──────────────
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
        "id": r.id, "material_key": r.material_key, "material_name": r.material_key,
        "year_month": r.year_month, "supplier": r.supplier,
        "actual_total": float(_d(r.actual_total)), "note": r.note,
    } for r in rows]


def save_monthly_recon(db: Session, *, material_key: str, year_month: str, actual_total,
                       supplier: Optional[str] = None, note: Optional[str] = None,
                       recon_id: Optional[int] = None) -> dict:
    """录入/更新 某分类某月工厂返回的对账总额。recon_id 给了=更新, 否则新增(同分类同月可多供应商)。"""
    material_key = (material_key or "").strip()
    if not material_key:
        raise ValueError("分类不能为空")
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
    return {"id": row.id, "material_key": row.material_key, "material_name": row.material_key,
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
    """导当月(发货月)已发货成交订单清单。

    material_key(=分类)空 = 全部发货单(基础列, 工厂自挑); 给了 = 只列 BOM 里有该分类外采配件的单,
    逐单展开该分类的配件部位 + 预设尺寸(去重、排木作)。按发货日期口径(ship_date)。
    """
    orders = _orders_shipped_in(db, year_month)
    out_orders: list[dict] = []
    t_est = Decimal("0")

    if material_key:
        from app.services import sku_utils
        mat_info, bom_by_pcsku, bom_by_pc = _load_bom_and_materials(db)
        for o in orders:
            cons = _order_category_consumption(o, mat_info, bom_by_pcsku, bom_by_pc).get(material_key)
            if not cons:
                continue
            parts = sorted(cons["materials"], key=lambda p: p["part_name"])
            est = cons["amount"]
            t_est += est
            out_orders.append({
                "order_no": o.order_no,
                "ship_date": o.ship_date.isoformat() if o.ship_date else None,
                "customer_name": o.customer_name,
                "product_name": o.product_name,
                "sku": o.sku,
                "est_parts": float(est),
                "is_custom": bool(getattr(o, "is_custom", False))
                or sku_utils.is_custom_sku_code(o.sku_code, o.product_code),
                "bom_parts": [{
                    "part_name": p["part_name"], "material_code": p["material_code"],
                    "category": p["category"], "qty": p["qty"], "unit": p["unit"],
                    "size_note": p["size_note"],
                    "size_uncertain": p.get("size_uncertain", False),
                    "alt_size_count": p.get("alt_size_count", 0),
                } for p in parts],
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
        "material_name": material_key,
        "order_count": len(out_orders),
        "total_est_parts": float(t_est.quantize(_CENTS)),
        "orders": out_orders,
    }
