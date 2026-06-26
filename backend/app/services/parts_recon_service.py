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

from app.models.order import Order, PartPurchase
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


def bulk_material_recon(db: Session, *, granularity: str = "month") -> dict:
    """大宗/消耗材料对账: 每材料每周期 实际采购 vs 标准消耗 vs 差异%。

    **消费窗口按订单 ship_date(发货日期)圈定** —— 生产周期~30天, 料在发货前才裁切消耗。
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

        periods = sorted(set(actual_by_p) | set(std_by_p) | set(cnt_by_p))
        rows = []
        t_actual = t_std = Decimal("0")
        for ym in periods:
            actual = actual_by_p.get(ym, Decimal("0")).quantize(_CENTS)
            std = std_by_p.get(ym, Decimal("0")).quantize(_CENTS)
            var = (actual - std).quantize(_CENTS)
            var_pct = float((var / std * 100).quantize(_CENTS)) if std > 0 else None
            t_actual += actual
            t_std += std
            rows.append({
                "period": ym,
                "actual_purchase": float(actual),
                "standard_consume": float(std),
                "variance": float(var),
                "variance_pct": var_pct,
                "order_count": cnt_by_p.get(ym, 0),
                "missing_est": miss_by_p.get(ym, 0),
            })
        t_var = (t_actual - t_std).quantize(_CENTS)
        materials_out.append({
            "key": mat["key"],
            "name": mat["name"],
            "mode": mode,
            "periods": rows,
            "total_actual": float(t_actual),
            "total_standard": float(t_std),
            "total_variance": float(t_var),
            "total_variance_pct": float((t_var / t_std * 100).quantize(_CENTS)) if t_std > 0 else None,
        })

    return {"granularity": granularity, "ship_date_basis": True, "materials": materials_out}
