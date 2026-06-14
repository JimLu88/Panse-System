"""订单理论成本反推 (按 BOM × 物料单价).

- 理论成本(单件) = Σ(该 SKU 的 BOM 每条 qty_per_product × 单价).
- 木作物料 (WD- 前缀) 不在 materials 表, 单价按 SKU 从定价表 PricingSku.wood_cost 取
  (木作成本是按 SKU 定制变化的, 整套木作部分共用这一个值).
- 实际成本不反推, 仅由人工/导入录入.
- 差异 = 实际成本 − 理论成本 (任一缺失则 None), 由 Order.cost_diff 属性给出.
- compute() 返回逐条物料 breakdown, 供前端把反推过程可视化.

另: backfill_theoretical_from_pricing() 直接用定价表 accounting_cost (会计总成本)
回填 order.theoretical_cost — 适合冷启动 / 缺 BOM 时, 把定价表当单一真值来源。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bom import BomLine
from app.models.material import Material
from app.models.order import Order
from app.models.pricing import PricingSku
from app.services import sku_utils

_CENTS = Decimal("0.01")

# 木作物料编码前缀: 这类物料不在 materials 单价表, 价格按 SKU 从 PricingSku.wood_cost 取。
WOOD_PREFIX = "WD"

# 零成本订单关键词: 商家安装 / 送货入户 / 补差价 / 淘宝官方服务 这类 SKU 无实际生产成本, 自动归0不报异常。
ZERO_COST_KEYWORDS = ("安装", "官方服务", "上门服务", "送货", "补差价")


def default_warehouse_for(product_name: Optional[str], sku: Optional[str],
                          is_refill: bool) -> str:
    """发货仓库默认判定: 样块 / 补单订单统一杭州, 其余默认江西仓库。"""
    text = f"{product_name or ''} {sku or ''}"
    if is_refill or "样块" in text or "样品" in text:
        return "杭州"
    return "江西仓库"


def zero_cost_reason(order: Order) -> Optional[str]:
    """判断订单是否应把理论成本直接归 0, 返回原因 (否则 None)。

    - 补单/刷单: 不产生真实生产成本 (成本在补单记录单独核算), 理论成本=0。
    - 商家安装 / 淘宝官方服务 SKU: 淘宝官方服务, 无实际成本, 全部=0。
    """
    if order.is_refill:
        return "补单(刷单)无真实生产成本, 成本在补单记录核算"
    text = f"{order.sku or ''} {order.sku_code or ''} {order.product_name or ''}"
    if any(k in text for k in ZERO_COST_KEYWORDS):
        return "商家安装/淘宝官方服务 SKU, 无实际成本"
    return None


@dataclass
class CostLine:
    material_code: str
    material_name: Optional[str]
    qty_per_product: Decimal
    unit_price: Optional[Decimal]
    line_cost: Optional[Decimal]      # qty_per_product × unit_price
    missing_price: bool


@dataclass
class CostBreakdown:
    order_no: str
    sku_code: Optional[str]
    qty: int
    unit_cost: Decimal                # 单件理论成本 = Σ line_cost
    total_cost: Decimal               # unit_cost × qty
    lines: list[CostLine] = field(default_factory=list)
    resolved: bool = False            # 是否成功匹配到 BOM
    missing_price_count: int = 0
    cost_incomplete: bool = False     # 有物料/木作单价未知(NULL) → 该成本不完整, 非权威值
    note: Optional[str] = None


def _resolve_sku_code(db: Session, order: Order) -> Optional[str]:
    """订单上 sku_code 优先 (去掉定制「改」后缀取基础码查 BOM); 否则用 SKU 名反查."""
    if order.sku_code:
        return sku_utils.strip_custom_suffix(order.sku_code)
    if order.sku:
        ps = db.execute(
            select(PricingSku).where(PricingSku.sku == order.sku)
        ).scalar_one_or_none()
        if ps:
            return ps.sku_code
    return None


def _wood_unit_price(db: Session, sku_code: Optional[str]) -> Optional[Decimal]:
    """木作物料单价: 按 SKU 从定价表 PricingSku.wood_cost 取 (整套木作共用此值)."""
    if not sku_code:
        return None
    wc = db.execute(
        select(PricingSku.wood_cost).where(PricingSku.sku_code == sku_code)
    ).scalar_one_or_none()
    return Decimal(str(wc)) if wc is not None else None


def compute(db: Session, order: Order) -> CostBreakdown:
    """反推一条订单的理论成本, 返回明细 (不写库).

    口径 (用户拍板 2026-06-12): **已知 SKU 直接用定价表工厂成本反推**
    (factory_cost 含全套配件的批量采购价) + 运费/安装/税/扣点 = 会计总成本;
    BOM 物料反推只用于 **定制单** (BOM 配件是单件订购价, 对成品 SKU 不准)。

    BOM 路径细节: 普通物料按 materials.price; 木作物料 (WD- 前缀) 按定价表
    PricingSku.wood_cost, 整套只计第一条 WD 行, 避免重复累加。
    """
    sku_code = _resolve_sku_code(db, order)
    _is_custom_order = order.is_custom or sku_utils.is_custom_sku_code(order.sku_code, order.product_code)

    # ---- 已知 SKU (非定制): 定价表直推, 不走 BOM ----
    if sku_code and not _is_custom_order:
        ps = db.execute(
            select(PricingSku).where(PricingSku.sku_code == sku_code)
        ).scalar_one_or_none()
        if ps is not None and (ps.factory_cost is not None or ps.accounting_cost is not None):
            lines: list[CostLine] = []
            for code, name, val in (
                ("工厂成本", "出厂成本(定价表, 含全套配件)", ps.factory_cost),
                ("运费", "物流运输费(定价表)", ps.logistics_cost),
                ("安装", "安装费(定价表)", ps.install_cost),
                ("税费", "税费(定价表)", ps.tax),
                ("扣点", "平台扣点(定价表)", ps.platform_fee_rate),  # 列名为 rate, 实存金额
            ):
                if val is None or Decimal(str(val)) == 0:
                    continue
                amt = Decimal(str(val)).quantize(_CENTS)
                lines.append(CostLine(
                    material_code=code, material_name=name,
                    qty_per_product=Decimal("1"), unit_price=amt,
                    line_cost=amt, missing_price=False,
                ))
            comp_sum = sum(
                (ln.line_cost for ln in lines if ln.line_cost is not None), Decimal("0")
            ).quantize(_CENTS)
            # 合计以定价表会计总成本为准 (组件缺列时兜底用组件合计)
            unit_cost = (Decimal(str(ps.accounting_cost)).quantize(_CENTS)
                         if ps.accounting_cost is not None else comp_sum)
            qty = int(order.qty or 1)
            note = "口径: 定价表工厂成本+费用 (已知 SKU 不走 BOM, BOM 仅用于定制估算)"
            if ps.accounting_cost is not None and comp_sum != unit_cost:
                note += f" | 组件合计 ¥{comp_sum} 与会计总成本差 ¥{(unit_cost - comp_sum).quantize(_CENTS)}"
            return CostBreakdown(
                order_no=order.order_no, sku_code=sku_code, qty=qty,
                unit_cost=unit_cost,
                total_cost=(unit_cost * qty).quantize(_CENTS),
                lines=lines, resolved=True,
                missing_price_count=0, cost_incomplete=False, note=note,
            )

    # ---- 定制单 / 定价表无成本: BOM 物料反推 ----
    lines: list[CostLine] = []
    if sku_code:
        wood_price = _wood_unit_price(db, sku_code)
        wood_counted = False  # 整套木作成本只计一次
        rows = db.execute(
            select(BomLine, Material.name.label("mat_name"), Material.price.label("price"))
            .join(Material, BomLine.material_code == Material.code, isouter=True)
            .where(BomLine.sku_code == sku_code)
        ).all()
        for bom, mat_name, price in rows:
            qty_per = Decimal(str(bom.qty_per_product or 1))
            is_wood = (bom.material_code or "").upper().startswith(WOOD_PREFIX)
            if is_wood:
                # 木作: 单价取定价表 wood_cost, 整套只计第一条, 其余 WD 行计 0
                if wood_counted:
                    p = Decimal("0")
                else:
                    p = wood_price
                    wood_counted = wood_price is not None
                # 木作成本是整套固定值, 不乘 qty_per (qty_per 仅描述拆件份数)
                line_cost = p.quantize(_CENTS) if p is not None else None
                missing = wood_price is None
            else:
                p = Decimal(str(price)) if price is not None else None
                line_cost = (qty_per * p).quantize(_CENTS) if p is not None else None
                missing = p is None
            lines.append(CostLine(
                material_code=bom.material_code,
                material_name=mat_name,
                qty_per_product=qty_per,
                unit_price=p,
                line_cost=line_cost,
                missing_price=missing,
            ))

    # 费用组件 (用户 2026-06-12: 理论成本须含 运费/安装/税费/平台扣点 — 这些是实际发生的):
    # 来自定价表同 SKU 行。platform_fee_rate 列实存的是扣点金额 (账面核算:
    # accounting_cost = factory_cost + logistics + install + tax + platform_fee, 已实测吻合)。
    pricing_acc: Optional[Decimal] = None
    if lines and sku_code:
        ps = db.execute(
            select(PricingSku).where(PricingSku.sku_code == sku_code)
        ).scalar_one_or_none()
        if ps is not None:
            if ps.accounting_cost is not None:
                pricing_acc = Decimal(str(ps.accounting_cost))
            for code, name, val in (
                ("运费", "物流运输费(定价表)", ps.logistics_cost),
                ("安装", "安装费(定价表)", ps.install_cost),
                ("税费", "税费(定价表)", ps.tax),
                ("扣点", "平台扣点(定价表)", ps.platform_fee_rate),
            ):
                if val is None or Decimal(str(val)) == 0:
                    continue
                amt = Decimal(str(val)).quantize(_CENTS)
                lines.append(CostLine(
                    material_code=code, material_name=name,
                    qty_per_product=Decimal("1"), unit_price=amt,
                    line_cost=amt, missing_price=False,
                ))

    unit_cost = sum(
        (ln.line_cost for ln in lines if ln.line_cost is not None), Decimal("0")
    ).quantize(_CENTS)
    qty = int(order.qty or 1)
    total_cost = (unit_cost * qty).quantize(_CENTS)
    missing = sum(1 for ln in lines if ln.missing_price)

    wood_missing = any(
        ln.missing_price and (ln.material_code or "").upper().startswith(WOOD_PREFIX)
        for ln in lines
    )
    if not lines:
        note = "未匹配到 BOM (订单缺 sku_code 或该 SKU 无 BOM), 无法反推理论成本"
    elif missing and wood_missing:
        note = f"{missing} 项单价未知(待核算), 含木作成本 — 请在定价表补 wood_cost 后重算"
    elif missing:
        note = f"{missing} 项物料单价未知(待核算) — 请在物料表补单价后重算"
    else:
        note = None
    # 与定价表会计总成本对账: 差异通常 = BOM 反推出厂成本 与 定价表出厂成本的差
    if pricing_acc is not None and lines:
        diff = (unit_cost - pricing_acc).quantize(_CENTS)
        cmp_note = (f"定价表会计总成本 ¥{pricing_acc}"
                    + (f" (反推差 {'+' if diff > 0 else ''}{diff})" if diff != 0 else " (反推一致)"))
        note = (note + " | " if note else "") + cmp_note

    # 方案B: is_custom 单(或 SKU编码带「改」后缀)在基础BOM成本上加一笔「定制加价」(可手改, 来自定制报价单或手填)
    if _is_custom_order and order.custom_surcharge is not None:
        sur = Decimal(str(order.custom_surcharge)).quantize(_CENTS)
        if sur != 0:
            had_base = bool(lines)
            lines.append(CostLine(
                material_code="定制加价",
                material_name="定制加价(方案B)",
                qty_per_product=Decimal("1"),
                unit_price=sur,
                line_cost=sur,
                missing_price=False,
            ))
            unit_cost = (unit_cost + sur).quantize(_CENTS)
            total_cost = (unit_cost * qty).quantize(_CENTS)
            note = (note + " | " if note else "") + f"含定制加价 ¥{sur}"
            if not had_base:
                note += "(⚠️ 基础BOM未匹配, 仅含加价)"

    return CostBreakdown(
        order_no=order.order_no,
        sku_code=sku_code,
        qty=qty,
        unit_cost=unit_cost,
        total_cost=total_cost,
        lines=lines,
        resolved=bool(lines),
        missing_price_count=missing,
        cost_incomplete=missing > 0,
        note=note,
    )


def cost_completeness_scan(db: Session) -> dict:
    """成本完整性体检: 找定价表里关键成本列为空(未知/待补)的 SKU。

    区别 0 (确实免费) 与 NULL (未知): NULL 表示成本不完整, 任何由它派生的
    标价/理论成本都不是权威值, 这里把这些 SKU 列出来供前端标「成本不完整 ⚠️」,
    而不是让系统拿一个偏低的数字当真。
    """
    skus = db.execute(select(PricingSku)).scalars().all()
    incomplete: list[dict] = []
    for s in skus:
        missing = [name for name, val in (
            ("木作成本", s.wood_cost),
            ("总出厂成本", s.factory_cost),
            ("会计总成本", s.accounting_cost),
        ) if val is None]
        if missing:
            incomplete.append({"sku_code": s.sku_code, "missing": missing})
    return {
        "total_skus": len(skus),
        "incomplete_count": len(incomplete),
        "incomplete": incomplete[:300],
    }


def recompute_and_save(db: Session, order: Order) -> CostBreakdown:
    """反推并把单件理论成本写回 order.theoretical_cost (不动 actual_cost).

    补单/安装SKU 直接归 0, 不走 BOM 反推。
    """
    reason = zero_cost_reason(order)
    if reason is not None:
        order.theoretical_cost = Decimal("0")
        return CostBreakdown(
            order_no=order.order_no, sku_code=order.sku_code, qty=int(order.qty or 1),
            unit_cost=Decimal("0"), total_cost=Decimal("0"),
            resolved=True, note=f"理论成本归0: {reason}",
        )
    bd = compute(db, order)
    if bd.resolved:
        order.theoretical_cost = bd.unit_cost
        return bd
    # 无 BOM → 回退定价表会计总成本 (口径同样含运费/安装/税/扣点)
    cost = _pricing_cost_for(db, order)
    if cost is not None:
        order.theoretical_cost = cost.quantize(_CENTS)
        bd.unit_cost = cost.quantize(_CENTS)
        bd.total_cost = (bd.unit_cost * bd.qty).quantize(_CENTS)
        bd.resolved = True
        bd.note = ((bd.note + " | ") if bd.note else "") + "已回退定价表会计总成本"
    return bd


def recompute_all(db: Session, *, only_missing: bool = True) -> dict:
    """批量反推. only_missing=True 时补 theoretical_cost 为空 **或为 0** 的订单
    (历史导入曾把未反推订单写成 0, 只查 NULL 会让这批永远卡死, 2026-06-12 修);
    真正应为 0 的 (补单/安装SKU) 由 zero_cost_reason 幂等重新归 0, 不受影响。

    Returns: {updated, skipped_no_bom, total}
    """
    from sqlalchemy import or_
    stmt = select(Order)
    if only_missing:
        stmt = stmt.where(or_(Order.theoretical_cost.is_(None),
                              Order.theoretical_cost == 0))
    orders = db.execute(stmt).scalars().all()
    updated = skipped = 0
    for o in orders:
        bd = recompute_and_save(db, o)
        if bd.resolved:
            updated += 1
        else:
            skipped += 1
    return {"updated": updated, "skipped_no_bom": skipped, "total": len(orders)}


# ----------------------------- 定价表回填 ---------------------------- #

# 交易关闭/取消的订单无需理论成本 (不影响利润核算)
_CLOSED_STATUSES = {"cancelled"}


def _pricing_cost_for(db: Session, order: Order) -> Optional[Decimal]:
    """按 sku_code (其次 product_code) 从定价表取会计总成本 accounting_cost。"""
    sku_code = _resolve_sku_code(db, order)
    if sku_code:
        c = db.execute(
            select(PricingSku.accounting_cost).where(PricingSku.sku_code == sku_code)
        ).scalar_one_or_none()
        if c is not None:
            return Decimal(str(c))
    # 退一步: 用 product_code 取该产品任一有成本的定价行 (同产品不同 SKU 成本接近)
    if order.product_code:
        c = db.execute(
            select(PricingSku.accounting_cost)
            .where(PricingSku.product_code == order.product_code,
                   PricingSku.accounting_cost.isnot(None))
            .limit(1)
        ).scalar_one_or_none()
        if c is not None:
            return Decimal(str(c))
    return None


def backfill_theoretical_from_pricing(
    db: Session, *, only_missing: bool = True, skip_closed: bool = True,
) -> dict:
    """用定价表 accounting_cost 回填 order.theoretical_cost (单一真值来源).

    与 recompute_all (按 BOM 反推) 互补: 这个直接信任定价表的会计总成本,
    适合冷启动 / 缺 BOM 的订单。

    - only_missing: 仅补 theoretical_cost 为空的订单
    - skip_closed: 跳过 cancelled 订单 (交易关闭无需理论成本)

    Returns: {updated, skipped_no_pricing, skipped_closed, total}
    """
    stmt = select(Order)
    if only_missing:
        stmt = stmt.where(Order.theoretical_cost.is_(None))
    orders = db.execute(stmt).scalars().all()
    updated = no_pricing = closed = zeroed = 0
    for o in orders:
        if skip_closed and o.status in _CLOSED_STATUSES:
            closed += 1
            continue
        # 补单/安装SKU → 直接归 0, 不查定价表
        if zero_cost_reason(o) is not None:
            o.theoretical_cost = Decimal("0")
            zeroed += 1
            continue
        cost = _pricing_cost_for(db, o)
        if cost is not None:
            o.theoretical_cost = cost.quantize(_CENTS)
            updated += 1
        else:
            no_pricing += 1
    db.flush()
    return {
        "updated": updated,
        "zeroed_refill_install": zeroed,
        "skipped_no_pricing": no_pricing,
        "skipped_closed": closed,
        "total": len(orders),
    }
