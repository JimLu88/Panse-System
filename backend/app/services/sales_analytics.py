"""销售统计 + 预测 + 备货建议 (Phase 4, 业务需求 7/8/15/16).

主要 API:
    summary(db, start, end)           — 一段时间内的销售汇总 + 利润排行
    product_breakdown(db, start, end) — 分产品销售明细
    forecast_30d(db)                  — 移动平均预测未来 30 天每个 SKU 销量
    stock_advice(db)                  — 备货建议: 基于预测 + 现库存 + 物料 lead_time

成本估算: 简单方案 — 直接用 Order.theoretical_cost / actual_cost; 都为空时用 0.
对于 historical=True 的订单, 全部跳过 (不入统计)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import and_, func, not_, or_, select
from sqlalchemy.orm import Session

from app.models.bom import BomLine
from app.models.inventory import PartInventory, ProductInventory
from app.models.material import Material
from app.models.order import FactoryOrder, Order
from app.models.product import Product
from app.services import product_coder

# ── 真实成交订单口径 (用户拍板 2026-06-17) ─────────────────────────────────────
# 只算「买家已付款且成交」的单, 全系统统一口径, 不能疏漏:
#   排除 待付款(pending_payment) / 取消 / 关闭, 以及 全额退款单。
#   补单(is_refill) 由各调用方按需另行过滤 (有的要单独统计补单)。
SETTLED_SALE_STATUSES = ("paid", "shipped", "signed", "completed", "success", "finished")


# 服务行项 (送货入户/商家安装/上门), 非产品销售 → 不计营收/成本 (用户拍板 2026-06-18)。
SERVICE_KEYWORDS = ("送货", "入户", "安装", "上门")


def _is_service_line(name) -> bool:
    n = name or ""
    return any(k in n for k in SERVICE_KEYWORDS)


def settled_sale_clause():
    """SQL 条件: 已付款成交(非待付款/取消/关闭) 且 未全额退款 且 非纯服务行项。不含 is_refill 过滤。

    口径修正 (用户实测 2026-06-18):
    - 全额退款含 paid=0 但有退款的"退款单"(原只判 paid>0 → 那种单算出 −退款 的负收入)。
    - 服务行项(送货/安装)**只在 实付=0(免费附加行) 时排除**; 有实付的是被误标成"送货入户"的真实产品单(如¥11212的餐边柜), 必须保留。
    """
    paid = func.coalesce(Order.paid_amount, 0)
    refund = func.coalesce(Order.refund_amount, 0)
    name = func.coalesce(Order.product_name, "")
    is_service = or_(*[name.contains(k) for k in SERVICE_KEYWORDS])
    return and_(
        Order.status.in_(SETTLED_SALE_STATUSES),
        paid > 0,                                                   # 必须有真实付款: 关闭/未付款单(实付0)不算成交
        not_(and_(refund > 0, refund >= paid * Decimal("0.99"))),  # 全额退款不算成交
        not_(and_(is_service, paid <= 0)),                          # 仅排除 ¥0 的纯服务附加行
    )


def is_settled_sale(o) -> bool:
    """Python 版同口径 (遍历订单对象时用)。"""
    if (getattr(o, "status", "") or "") not in SETTLED_SALE_STATUSES:
        return False
    paid = Decimal(str(o.paid_amount or 0))
    refund = Decimal(str(getattr(o, "refund_amount", 0) or 0))
    if paid <= 0:                                          # 关闭/未付款单(实付0)不算成交
        return False
    if refund > 0 and refund >= paid * Decimal("0.99"):   # 全额退款
        return False
    if _is_service_line(getattr(o, "product_name", "")) and paid <= 0:  # 仅 ¥0 纯服务附加行
        return False
    return True


@dataclass
class SalesSummary:
    period_start: date
    period_end: date
    order_count: int = 0
    revenue: Decimal = Decimal("0")
    cost: Decimal = Decimal("0")
    gross_profit: Decimal = Decimal("0")        # revenue - cost - freight 等
    net_profit: Decimal = Decimal("0")           # gross - 安装 - 上楼费 - 补偿
    top_products_by_profit: list[dict] = field(default_factory=list)
    top_products_by_profit_rate: list[dict] = field(default_factory=list)
    bottom_products_by_profit: list[dict] = field(default_factory=list)  # 低利润榜: 亏得最多在前


def _profit_for(o: Order, coef: dict, aftersales: Decimal) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """统一会计成本口径 (用户拍板 2026-06-18 全系统同口径): 返回 (真实收入, 会计总成本, 利润, 物理成本)。
    真实收入=实付−退款; 会计总成本=物理+物流+安装/上楼+额外售后(按订单归属)+平台扣点(实付−实收)+税。"""
    from app.services import order_financials as ofin
    revenue = Decimal(o.paid_amount or 0) - Decimal(o.refund_amount or 0)   # 收入扣退款 (统一口径)
    return (revenue, ofin.accounting_cost(o, coef, aftersales=aftersales),
            ofin.net_profit(o, coef, aftersales=aftersales), ofin.physical_cost(o))


def brand_of(o: Order) -> Optional[str]:
    """Plan F8: 订单归属品牌 — 优先店铺名 (畔色→PS / 孚格→PFG), 缺失回退编码前缀。"""
    shop = o.shop or ""
    if "畔色" in shop:
        return "PS"
    if "孚格" in shop:
        return "PFG"
    code = (o.product_code or o.sku_code or o.sku or "")
    if code.startswith("PPS"):
        return "PS"
    if code.startswith("PFG"):
        return "PFG"
    return None


def summary(db: Session, *, start: date, end: date,
            platform: Optional[str] = None,
            brand: Optional[str] = None) -> SalesSummary:
    """汇总一段时间内已发货/签收订单的销售指标 (业务需求 15)。brand=PS/PFG 按品牌过滤 (F8)。"""
    q = select(Order).where(
        Order.order_date >= start,
        Order.order_date <= end,
        settled_sale_clause(),       # 已付款成交·非待付款/取消/关闭·未全额退款 (用户拍板 2026-06-17)
        Order.is_refill == False,    # noqa: E712  # 剔除补单/刷单(¥0成本会造成100%利润假象)
    )
    if platform:
        q = q.where(Order.platform == platform)
    orders = db.execute(q).scalars().all()
    if brand:
        orders = [o for o in orders if brand_of(o) == brand]

    # 产品名用内部短名 (Product.name), 不用淘宝长标题 (用户拍板 2026-06-17)
    name_map, _ = _internal_names(db, {o.product_code for o in orders if o.product_code})

    from app.services import order_financials as ofin
    coef = ofin.load_coefficients(db)
    as_by_order = ofin.extra_aftersales_by_order(db)   # 售后按订单归属 (统一口径)

    s = SalesSummary(period_start=start, period_end=end)
    by_product: dict[str, dict] = {}
    for o in orders:
        revenue, cost, net, phys = _profit_for(o, coef, Decimal(as_by_order.get(o.order_no, 0)))
        s.order_count += 1
        s.revenue += revenue
        s.cost += cost                  # 会计总成本(全扣项)
        s.net_profit += net
        s.gross_profit += revenue - phys  # 毛利 = 销售额 − 物理产品成本
        key = o.product_code or o.product_name or "未知"
        d = by_product.setdefault(key, {
            "product_code": o.product_code,
            "product_name": name_map.get(o.product_code) or o.product_name,
            "order_count": 0, "revenue": Decimal("0"),
            "cost": Decimal("0"), "net_profit": Decimal("0"),
        })
        d["order_count"] += 1
        d["revenue"] += revenue
        d["cost"] += cost
        d["net_profit"] += net

    # 利润排行 + 利润率排行
    rows = list(by_product.values())
    for r in rows:
        r["profit_rate"] = (
            (r["net_profit"] / r["revenue"]) if r["revenue"] > 0 else Decimal("0")
        )
    s.top_products_by_profit = sorted(rows, key=lambda r: r["net_profit"], reverse=True)[:10]
    s.top_products_by_profit_rate = sorted(
        rows, key=lambda r: r["profit_rate"], reverse=True,
    )[:10]
    # 低利润榜 (用户拍板 2026-06-17): 亏得最多(负值)在前; 只收真正亏损/微利的, 至少留 10 条
    s.bottom_products_by_profit = sorted(rows, key=lambda r: r["net_profit"])[:10]
    return s


def product_breakdown(
    db: Session, *, start: date, end: date,
    brand: Optional[str] = None,
) -> list[dict]:
    """分产品 SKU 维度的销售指标 (业务需求 16)。brand=PS/PFG 按品牌过滤 (F8)。"""
    orders = db.execute(
        select(Order).where(
            Order.order_date >= start, Order.order_date <= end,
            settled_sale_clause(),      # 已付款成交·非待付款/取消/关闭·未全额退款
            Order.is_refill == False,   # noqa: E712  # 剔除补单/刷单(用户拍板 2026-06-17)
        )
    ).scalars().all()
    if brand:
        orders = [o for o in orders if brand_of(o) == brand]
    # 产品名用内部短名 (Product.name), 不用淘宝长标题 (用户拍板 2026-06-17)
    name_map, _ = _internal_names(db, {o.product_code for o in orders if o.product_code})
    from app.services import order_financials as ofin
    coef = ofin.load_coefficients(db)
    as_by_order = ofin.extra_aftersales_by_order(db)   # 售后按订单归属 (统一口径)
    by_sku: dict[str, dict] = {}
    for o in orders:
        revenue, cost, net, phys = _profit_for(o, coef, Decimal(as_by_order.get(o.order_no, 0)))
        key = (o.product_code or "?", o.sku_code or o.sku or "?")
        d = by_sku.setdefault("|".join(key), {
            "product_code": o.product_code,
            "product_name": name_map.get(o.product_code) or o.product_name,
            "sku_code": o.sku_code, "sku": o.sku,
            "qty": 0, "revenue": Decimal("0"), "phys": Decimal("0"),
            "cost": Decimal("0"), "net_profit": Decimal("0"),
        })
        d["qty"] += o.qty or 1
        d["revenue"] += revenue
        d["cost"] += cost              # 会计总成本
        d["phys"] += phys              # 物理成本 (算毛利率用)
        d["net_profit"] += net
    for r in by_sku.values():
        r["gross_profit_rate"] = (
            ((r["revenue"] - r["phys"]) / r["revenue"]) if r["revenue"] > 0 else Decimal("0")
        )
        r["net_profit_rate"] = (
            (r["net_profit"] / r["revenue"]) if r["revenue"] > 0 else Decimal("0")
        )
    return sorted(by_sku.values(), key=lambda r: r["revenue"], reverse=True)


# ----------------------------- 预测 ----------------------------- #


def _sales_by_day(db: Session, days: int = 90,
                  custom: Optional[bool] = None) -> dict[str, dict[date, int]]:
    """过去 N 天每个 SKU 每天的销量. 返回 {sku_key: {date: qty}}.

    key 永远是 'product_code|sku_id' 形式 (无 product_code 用 '?', 无 sku 用 product_code).
    custom: None=全部 / False=只常规单 / True=只定制单 (Order.is_custom)。
    """
    cutoff = date.today() - timedelta(days=days)
    # 只排补单 (刷的不是真实需求)。不排 is_historical: 本库导入路径给几乎所有
    # 真实订单都打了 historical 标 (529/530), 它不等于"旧数据", 排它=预测全空。
    conds = [
        Order.order_date >= cutoff,
        Order.status.in_(("paid", "shipped", "signed")),
        Order.is_refill == False,      # noqa: E712
    ]
    if custom is not None:
        conds.append(Order.is_custom == custom)  # noqa: E712
    orders = db.execute(select(Order).where(*conds)).scalars().all()
    out: dict[str, dict[date, int]] = {}
    for o in orders:
        if not o.order_date:
            continue
        pc = o.product_code or "?"
        sk = o.sku_code or o.sku or pc
        key = f"{pc}|{sk}"
        out.setdefault(key, {})
        out[key][o.order_date] = out[key].get(o.order_date, 0) + (o.qty or 1)
    return out


def _product_info_map(db: Session, codes: set[str]) -> dict[str, tuple]:
    """product_code → (name, image_url), 批量查避免 N+1。"""
    if not codes:
        return {}
    rows = db.execute(
        select(Product.code, Product.name, Product.image_url).where(Product.code.in_(codes))
    ).all()
    return {c: (n, img) for c, n, img in rows}


# 备货预测智能排除 (用户拍板 2026-06-11): 这些"产品"不是实物备货项
_FORECAST_EXCLUDE_KW = ("全屋定制", "样块", "补差", "差价", "邮费", "定金", "运费")


def forecast_30d(db: Session, custom: Optional[bool] = None) -> list[dict]:
    """业务需求 7 + 8: 简单移动平均预测未来 30 天销量 (按产品聚合)。

    custom: None=全部 / False=只常规单 / True=只定制单。

    用过去 60 天平均日销 × 30, 加 1.2 倍安全系数。
    排除补单 (is_refill); 用户拍板 (2026-06-11):
      - 按「产品」排列, 各 SKU (含定制咨询类) 归并到所属产品, SKU 明细放 skus 里
      - 全屋定制/样块/补差 等非实物备货项智能排除
      - 订单缺产品编码的 (旧版显示 "?") 不进备货预测

    返回: [{product_code, product_name, image_url, avg_daily, forecast_30d,
            last_60d_total, sku, skus: [{sku, qty_60d}]}]
    """
    by_sku = _sales_by_day(db, days=60, custom=custom)
    # 先按产品聚合
    by_product: dict[str, dict] = {}
    for sku_key, day_map in by_sku.items():
        total = sum(day_map.values())
        product_code, _, sku = sku_key.partition("|")
        if product_code == "?" or not product_code:
            continue   # 缺产品编码的订单无法备货, 不进预测 (在异常中心另行治理)
        g = by_product.setdefault(product_code, {"total": 0, "skus": []})
        g["total"] += total
        g["skus"].append({"sku": sku or "(未填SKU)", "qty_60d": total})
    info = _product_info_map(db, set(by_product))
    # 产品表查不到的编码 (订单 product_code 填错/产品未建档) → 用订单淘宝标题兜底,
    # 别再显示 "—" 让人猜 (2026-06-11 用户反馈)
    missing_codes = [c for c in by_product if c not in info or not info[c][0]]
    taobao_fallback: dict[str, str] = {}
    if missing_codes:
        for code, tname in db.execute(
            select(Order.product_code, func.max(Order.product_name))
            .where(Order.product_code.in_(missing_codes))
            .group_by(Order.product_code)
        ).all():
            if tname:
                taobao_fallback[code] = f"[产品表无此编码] {tname[:24]}"
    out = []
    for code, g in by_product.items():
        name, img = info.get(code, (None, None))
        if not name:
            name = taobao_fallback.get(code)
        if name and any(kw in name for kw in _FORECAST_EXCLUDE_KW):
            continue   # 非实物备货项
        avg_daily = g["total"] / 60
        forecast = int(avg_daily * 30 * 1.2 + 0.5)   # +20% 安全系数
        skus = sorted(g["skus"], key=lambda s: s["qty_60d"], reverse=True)
        out.append({
            "product_code": code,
            "product_name": name,
            "image_url": img,
            "avg_daily": round(avg_daily, 3),
            "forecast_30d": forecast,
            "last_60d_total": g["total"],
            "sku": None,           # 兼容旧字段 (现按产品聚合)
            "skus": skus,
        })
    return sorted(out, key=lambda r: r["forecast_30d"], reverse=True)


# ----------------------------- 备货建议 ------------------------- #


def _canon(pc: str) -> str:
    """品牌变体(PPS/PFG/P…)归一到稳定代表码, 供在产/库存按物理实物归并 (与成品库存 ABC 同口径)。"""
    vs = product_coder.brand_variants(pc) or {pc}
    return min(vs)


def _in_production_split(db: Session) -> tuple[dict[str, float], dict[str, float]]:
    """在产/在途(未到货未作废工厂单)拆两类 —— 完整 ATP 口径, 按规范产品码归集:

      free      = 备货单(source_order_id 为空): 会进可售现货 → **抵未来预测缺口**。
      allocated = 客户单 MTO(source_order_id 有值): 已卖给下单客户、到货即发走 → **不抵未来缺口**。

    返回 (free_map, allocated_map)。这样"需生产"只减真正能补充库存的自由在产,
    不会被"已排产给客户的量"误消成 0。
    """
    rows = db.execute(
        select(FactoryOrder.product_code, FactoryOrder.source_order_id,
               func.coalesce(func.sum(FactoryOrder.qty), 0)).where(
            FactoryOrder.actual_delivery.is_(None),   # 未到货 = 还在产/在途
            FactoryOrder.voided_at.is_(None),          # 未作废
            FactoryOrder.product_code.isnot(None),
        ).group_by(FactoryOrder.product_code, FactoryOrder.source_order_id)
    ).all()
    free: dict[str, float] = {}
    alloc: dict[str, float] = {}
    for pc, source_order_id, qty in rows:
        if not pc:
            continue
        c = _canon(pc)
        if source_order_id is None:
            free[c] = free.get(c, 0.0) + float(qty or 0)
        else:
            alloc[c] = alloc.get(c, 0.0) + float(qty or 0)
    return free, alloc


def _bom_material_need(db: Session, forecast: list[dict], *, use_stock: bool,
                       in_prod_free: Optional[dict] = None, in_prod_alloc: Optional[dict] = None):
    """按预测 + BOM 倒推每个物料的需求量。返回 (material_need{物料码:量}, products_out)。
    use_stock=True: 需生产 = max(预测 − 现货 − **自由在产(备货单)**, 0)(只扣会入库的自由在产,
                    客户单在产另列不抵扣); False: 全量(定制无成品可抵, 也不减在产)。"""
    material_need: dict[str, Decimal] = {}
    products_out = []
    for f in forecast:
        pc = f["product_code"]
        if not pc:
            continue
        if use_stock:
            pinv = db.execute(select(ProductInventory).where(
                ProductInventory.product_code == pc).limit(1)).scalar_one_or_none()
            in_stock = float(pinv.physical_qty) if pinv else 0.0
            free = float((in_prod_free or {}).get(_canon(pc), 0.0))    # 备货在产, 会入库 → 抵扣
            alloc = float((in_prod_alloc or {}).get(_canon(pc), 0.0))  # 客户单在产, 发客户 → 不抵扣
            need_to_produce = max(f["forecast_30d"] - in_stock - free, 0)
        else:
            in_stock, free, alloc, need_to_produce = 0.0, 0.0, 0.0, f["forecast_30d"]
        products_out.append({
            "product_code": pc, "product_name": f.get("product_name"),
            "image_url": f.get("image_url"), "sku": f.get("sku"),
            "forecast_30d": f["forecast_30d"], "in_stock": in_stock,
            "in_production_free": free,         # 备货在产(会入库, 已从需生产扣掉)
            "in_production_allocated": alloc,   # 客户单在产(发给下单客户, 不抵未来缺口)
            "need_to_produce": need_to_produce,
        })
        if need_to_produce <= 0:
            continue
        for line in db.execute(select(BomLine).where(BomLine.product_code == pc)).scalars().all():
            per = Decimal(line.qty_per_product or 0)
            add = (per * Decimal(need_to_produce)).quantize(Decimal("0.001"))
            material_need[line.material_code] = (
                material_need.get(line.material_code, Decimal("0")) + add)
    return material_need, products_out


def _materials_from_need(db: Session, need_map: dict, *, only_common: bool = False) -> list[dict]:
    """{物料码:需求量} → 物料备货建议(含现库存/提前期/建议下单日)。
    only_common=True: 只留**通用料**(Material.is_custom=False), 用于定制单可提前囤的料;
    定制专用料随单采购、不预囤, 故剔除。"""
    out = []
    today = date.today()
    for mat_code, need in need_map.items():
        mat = db.execute(select(Material).where(Material.code == mat_code)).scalar_one_or_none()
        if only_common and mat is not None and mat.is_custom:
            continue
        inv = db.execute(select(PartInventory).where(
            PartInventory.material_code == mat_code).limit(1)).scalar_one_or_none()
        have = float(inv.physical_qty) if inv else 0.0
        missing = float(need) - have
        lead = mat.lead_time_days if mat else 0
        alert_at = today + timedelta(days=max(30 - lead, 0))
        out.append({
            "material_code": mat_code,
            "material_name": mat.name if mat else None,
            "need_qty": float(need),
            "have_qty": have,
            "missing": missing,
            "lead_time_days": lead,
            "alert_at": alert_at.isoformat(),
            "should_order_now": missing > 0 and lead >= (30 - (alert_at - today).days),
            "priority": mat.priority if mat else "mid",
            "is_custom_material": bool(mat.is_custom) if mat else False,
        })
    return sorted(out, key=lambda m: m["missing"], reverse=True)


def stock_advice(db: Session) -> dict:
    """智能提前备货建议 —— 常规单 / 定制单 分开 (用户 2026-07-02)。

    常规单: 成品可提前生产/备货 → 产能缺口(预测−成品库存) + 全部物料需求。
    定制单: 成品无法预备(接单再产), 但**通用料可提前囤** → 只列通用料的备货计划,
            定制专用料(is_custom)随单采购、不预囤。
    返回: {products, materials(常规), custom_products, custom_materials(定制通用料)}。

    R1(ATP 真实缺口, 区分已占用): 需生产 = max(预测 − 现货 − 自由在产(备货单), 0)。
    客户单在产(已卖给下单客户)另列展示、不抵未来缺口; 定制段不减在产, 保守全量倒推通用料。
    """
    from app.services import product_inventory_service as _pis
    cfg = _pis.get_forecast_config(db)
    # 重点备货月(与成品库存页同口径): 预测按「目标月(今天+30天生产提前期)」季节系数缩放 ——
    # 4月自动为5-6月峰前瞻放大、7月自动比6月峰回落; 物料需求随缩放后的需生产联动。
    s_raw, s_month, s_mult = _pis._seasonal_effective_daily(cfg, 1.0, 30)
    fc_reg = forecast_30d(db, custom=False)
    fc_cus = forecast_30d(db, custom=True)
    if s_month is not None and abs(s_raw - 1.0) > 1e-9:
        for f in fc_reg:
            f["forecast_30d"] = int(round(f["forecast_30d"] * s_raw))
        for f in fc_cus:
            f["forecast_30d"] = int(round(f["forecast_30d"] * s_raw))

    free, alloc = _in_production_split(db)
    reg_need, products_out = _bom_material_need(
        db, fc_reg, use_stock=True, in_prod_free=free, in_prod_alloc=alloc)
    materials_out = _materials_from_need(db, reg_need)

    cus_need, custom_products = _bom_material_need(db, fc_cus, use_stock=False)
    custom_materials = _materials_from_need(db, cus_need, only_common=True)

    semi_enabled = bool(cfg.get("enable_semi_finished"))
    return {
        "products": products_out,
        "materials": materials_out,
        "custom_products": custom_products,
        "custom_materials": custom_materials,
        # 重点备货月: 预测/需生产/物料 已按目标月缩放(关=1.0原样); 前端横幅展示
        "seasonal": {"enabled": bool(cfg.get("enable_seasonal")),
                     "target_month": s_month, "multiplier": s_mult},
        # R5 半成品(白坯): 默认关闭时 semi_finished 为空、前端不显示; 打开后出池化备货计划
        "semi_finished_enabled": semi_enabled,
        "semi_finished": semi_finished_plan(db) if semi_enabled else [],
    }


def semi_finished_plan(db: Session) -> list[dict]:
    """R5 半成品(白坯)备货计划 —— 仅 enable_semi_finished 打开时有内容, 关闭返回 []。

    池化: 把共享同一白坯(semi_group)的各成品30天预测归集, 合并算白坯备货量, 波动比分开囤小。
      白坯备货量 = max(Σ该组成品预测 − 现有白坯 − 在产白坯, 0)。
    现阶段无独立半成品库存表, 现有/在产白坯暂按 0(打开功能+量大后再接半成品库存与在产)。
    """
    from app.services import product_inventory_service as _pis
    if not _pis.get_forecast_config(db).get("enable_semi_finished"):
        return []
    prods = db.execute(
        select(Product).where(Product.semi_finished_eligible == True)  # noqa: E712
    ).scalars().all()
    if not prods:
        return []
    fc = {f["product_code"]: f for f in forecast_30d(db, custom=None)}
    groups: dict[str, dict] = {}
    for p in prods:
        grp = p.semi_group or p.code
        g = groups.setdefault(grp, {"semi_group": grp, "members": [], "pooled_forecast": 0})
        row = fc.get(p.code)
        demand = int(row["forecast_30d"]) if row else 0
        g["members"].append({"product_code": p.code, "product_name": p.name, "forecast_30d": demand})
        g["pooled_forecast"] += demand
    from app.models.inventory import SemiFinishedInventory
    inv_rows = {s.semi_group: s for s in
                db.execute(select(SemiFinishedInventory)).scalars().all()}
    out = []
    for g in groups.values():
        s = inv_rows.get(g["semi_group"])
        on_hand = float(s.on_hand_qty) if s else 0.0        # 现有白坯 (半成品库存表)
        in_prod = float(s.in_production_qty) if s else 0.0  # 在产白坯
        need = max(g["pooled_forecast"] - on_hand - in_prod, 0)
        out.append({**g, "on_hand": on_hand, "in_production": in_prod, "recommend_semi": need})
    return sorted(out, key=lambda x: -x["recommend_semi"])


# ----------------------------- 滞销分类 ------------------------- #


def slow_moving_split(
    db: Session, *,
    long_no_sale_days: int = 60,
    overstock_ratio: float = 3.0,
) -> dict:
    """业务需求 8 滞销分类:
       1) 长期未售: last_outbound 距今 > N 天的成品
       2) 超大库存: 现库存 > overstock_ratio × 未来 30 天预测销量
    """
    today = date.today()
    cutoff = today - timedelta(days=long_no_sale_days)

    # 长期未售: 查 part_inventory.last_outbound_at
    long_idle: list[dict] = []
    rows = db.execute(select(PartInventory).where(PartInventory.physical_qty > 0)).scalars().all()
    mat_names = {
        c: n for c, n in db.execute(
            select(Material.code, Material.name).where(
                Material.code.in_({r.material_code for r in rows}))
        ).all()
    } if rows else {}
    for r in rows:
        if r.last_outbound_at and r.last_outbound_at < cutoff:
            long_idle.append({
                "material_code": r.material_code,
                "material_name": mat_names.get(r.material_code),
                "physical_qty": r.physical_qty,
                "last_outbound_at": r.last_outbound_at.isoformat() if r.last_outbound_at else None,
                "days_since": (today - r.last_outbound_at).days,
            })

    # 超大库存: 比对预测 (按 product_code 聚合, 同 product 多 SKU 求和)
    forecast = forecast_30d(db)
    fmap: dict[str, int] = {}
    for f in forecast:
        if f["product_code"]:
            fmap[f["product_code"]] = fmap.get(f["product_code"], 0) + f["forecast_30d"]
    overstock: list[dict] = []
    pinvs = db.execute(
        select(ProductInventory).where(ProductInventory.physical_qty > 0)
    ).scalars().all()
    pinfo = _product_info_map(db, {p.product_code for p in pinvs if p.product_code})
    for p in pinvs:
        forecast_qty = fmap.get(p.product_code, 0)
        if forecast_qty > 0 and p.physical_qty > overstock_ratio * forecast_qty:
            name, img = pinfo.get(p.product_code or "", (None, None))
            overstock.append({
                "product_code": p.product_code,
                "product_name": name,
                "image_url": img,
                "sku": p.sku,
                "physical_qty": p.physical_qty,
                "forecast_30d": forecast_qty,
                "ratio": round(p.physical_qty / forecast_qty, 2) if forecast_qty else None,
            })
    return {
        "long_idle": long_idle,
        "overstock": overstock,
        "thresholds": {
            "long_no_sale_days": long_no_sale_days,
            "overstock_ratio": overstock_ratio,
        },
    }


# ----------------------------- 销售排行榜 ----------------------- #


def _period_key(d: date, granularity: str) -> str:
    return f"{d.year:04d}-{d.month:02d}" if granularity == "month" else f"{d.year:04d}"


# 补差价 / 邮费 / 专拍链接不是真实产品 (买家拍大量 ¥1 凑差价), 会污染排行榜, 排除。
_NON_PRODUCT_KEYWORDS = ("差价", "邮费", "补拍", "专拍", "专链", "运费", "补差", "改价")


def _is_non_product(name: Optional[str]) -> bool:
    n = name or ""
    return any(k in n for k in _NON_PRODUCT_KEYWORDS)


def _internal_names(db: Session, codes: set[str]) -> tuple[dict[str, str], dict[str, str]]:
    """订单 product_code → 内部短名(Product.name) + 规范编码。

    处理品牌前缀漂移: 订单常用「P+11位」, 产品档案是「PPS+11位」(畔色)。
    返回 (name_map: code→内部短名, canon_map: code→规范编码; 同款 P/PPS 合并到 PPS)。
    与 dashboard_monthly_service.sales_mix(_iname) 同口径。"""
    want: set[str] = set()
    for c in codes:
        if not c:
            continue
        want.add(c)
        if c.startswith("P") and not c.startswith("PPS"):
            want.add("PPS" + c[1:])
    prod = (dict(db.execute(select(Product.code, Product.name).where(Product.code.in_(want))).all())
            if want else {})
    name_map: dict[str, str] = {}
    canon_map: dict[str, str] = {}
    for c in codes:
        if not c:
            continue
        if c in prod:
            name_map[c] = prod[c]
            canon_map[c] = c
        elif c.startswith("P") and not c.startswith("PPS") and ("PPS" + c[1:]) in prod:
            name_map[c] = prod["PPS" + c[1:]]
            canon_map[c] = "PPS" + c[1:]
        else:
            canon_map[c] = c
    return name_map, canon_map


def product_ranking(
    db: Session, *,
    granularity: str = "month",   # month(按月) / year(按年)
    metric: str = "revenue",      # revenue(销售额) / qty(销量) / profit(利润率)
    period: Optional[str] = None,  # 指定周期 (如 2026-04 / 2026); 缺省取最新
    limit: int = 30,
) -> dict:
    """销售排行榜: 按月/按年, 分产品的 销量/销售额/利润率 排行 + 每期冠军时间线。

    口径: 正式销售 (非补单 is_refill=False, 未取消, 有下单日期)。销售额=买家实付−退款。
    利润 (metric=profit, 用户 2026-06-25): 净利=实付−退款−会计总成本 (商品+物流+安装+平台扣点+税+
      额外售后), 与逐单核对/月度P&L 完全同口径; 利润率榜按 净利率(净利/销售额) 排序, 每行同时给出
      利润额(¥)与利润率(%), 便于看「哪个产品贡献利润」。注意: 此处为逐单净利之和(产品维度),
      不再分摊推广/人员/固定成本 (那些是区间级费用, 见月度经营)。
    返回 {granularity, metric, selected_period, periods[每期冠军], ranking[选定期排行]}。
    """
    granularity = "year" if granularity == "year" else "month"
    metric = metric if metric in ("qty", "profit") else "revenue"  # +利润率(profit) (用户 2026-06-25)

    from app.services import order_financials as ofin
    coef = ofin.load_coefficients(db)                 # 利润榜走全系统统一会计口径 (与月度P&L/逐单核对一致)
    as_by_order = ofin.extra_aftersales_by_order(db)  # 额外售后按订单归属 (退款不重复计)

    orders = db.execute(
        select(Order).where(
            Order.is_refill == False,  # noqa: E712
            Order.order_date.isnot(None),
            settled_sale_clause(),     # 已付款成交·非待付款/取消/关闭·未全额退款 (用户拍板 2026-06-17)
        )
    ).scalars().all()

    # #19/#25: 用内部短名(Product.name)替淘宝长名 + 合并 P↔PPS 前缀漂移去重
    name_map, canon_map = _internal_names(db, {o.product_code for o in orders if o.product_code})

    buckets: dict[str, dict[str, dict]] = {}
    excluded = 0
    for o in orders:
        if not o.order_date:
            continue
        if _is_non_product(o.product_name):
            excluded += 1
            continue
        pk = _period_key(o.order_date, granularity)
        code = o.product_code
        canon = canon_map.get(code, code) if code else None
        iname = name_map.get(code) if code else None
        name = iname or o.product_name or code or "未知产品"
        key = canon or name
        bp = buckets.setdefault(pk, {})
        d = bp.setdefault(key, {
            "product_code": canon, "product_name": name,
            "qty": 0, "revenue": Decimal("0"), "net_profit": Decimal("0"), "order_count": 0,
        })
        if iname and d["product_name"] != iname:
            d["product_name"] = iname     # 优先内部短名 (同款 P/PPS 合并后统一显示)
        d["qty"] += int(o.qty or 1)
        # #25 总销售额去退款: 实付 - 退款 (全退订单计 0)
        rev = Decimal(o.paid_amount or 0) - Decimal(o.refund_amount or 0)
        d["revenue"] += rev if rev > 0 else Decimal("0")
        # 净利 = 实付−退款−会计总成本; 与逐单核对/月度P&L 同口径 (order_financials.net_profit)
        d["net_profit"] += ofin.net_profit(o, coef, aftersales=Decimal(as_by_order.get(o.order_no, 0)))
        d["order_count"] += 1

    def _rate(np_: Decimal, rev: Decimal) -> float:
        """净利率 = 净利 / 销售额 (无营收→0)。"""
        return float(np_ / rev) if rev and rev > 0 else 0.0

    def metric_val(d: dict):
        if metric == "qty":
            return d["qty"]
        if metric == "profit":
            # 利润率榜: 按净利率排序; 无营收产品(理论上成交单不会出现)排末位
            return _rate(d["net_profit"], d["revenue"]) if d["revenue"] > 0 else -9.99
        return d["revenue"]

    periods_out: list[dict] = []
    for pk in sorted(buckets.keys(), reverse=True):
        rows = list(buckets[pk].values())
        champ = max(rows, key=metric_val) if rows else None
        total_profit = sum((r["net_profit"] for r in rows), Decimal("0"))
        total_revenue = sum((r["revenue"] for r in rows), Decimal("0"))
        periods_out.append({
            "period": pk,
            "champion_name": champ["product_name"] if champ else None,
            "champion_qty": int(champ["qty"]) if champ else 0,
            "champion_revenue": float(champ["revenue"]) if champ else 0.0,
            "champion_profit": float(champ["net_profit"]) if champ else 0.0,
            "champion_profit_rate": _rate(champ["net_profit"], champ["revenue"]) if champ else 0.0,
            "total_qty": int(sum(r["qty"] for r in rows)),
            "total_revenue": float(total_revenue),
            "total_profit": float(total_profit),
            "total_profit_rate": _rate(total_profit, total_revenue),
            "product_kinds": len(rows),
        })

    sel = period if (period and period in buckets) else (periods_out[0]["period"] if periods_out else None)
    ranking: list[dict] = []
    if sel and sel in buckets:
        rows = sorted(buckets[sel].values(), key=metric_val, reverse=True)[:limit]
        for i, r in enumerate(rows, 1):
            ranking.append({
                "rank": i,
                "product_code": r["product_code"],
                "product_name": r["product_name"],
                "qty": int(r["qty"]),
                "revenue": float(r["revenue"]),
                "net_profit": float(r["net_profit"]),       # 利润额 (¥) — 利润率榜旁显示 (用户要)
                "profit_rate": _rate(r["net_profit"], r["revenue"]),  # 利润率 (0~1)
                "order_count": r["order_count"],
            })

    return {
        "granularity": granularity, "metric": metric,
        "selected_period": sel,
        "periods": periods_out,
        "ranking": ranking,
        "excluded_non_product": excluded,  # 排除的补差价/邮费/专链等非产品订单数
        "refund_excluded": True,           # #25 总销售额=实付-退款 (已去退款)
    }
