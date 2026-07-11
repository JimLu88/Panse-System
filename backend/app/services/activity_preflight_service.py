"""活动报名「虚拟推送(预检)」—— 生成活动表【前】的体检 (2026-07-11 用户需求)。

不产文件、不改数据、不上传淘宝。把"如果现在生成三步活动表并上传会出什么问题"提前算出来:
  1. 坏价产品   : 各尺寸报名价雷同(占位价/复制价未真实定价), 会把废价带上淘宝 → 排除并提示先改价;
  2. 缺淘宝映射 : 有日常价但没 taobao_sku_id 的 SKU, 报名表里不会出现 → 淘宝报名会报"缺SKU";
  3. 15天最低价冲突: 计划大促到手(报名价A×0.88) 高于 近15天真实最低成交价 → 大促报名会被判"涨价"拦;
  4. 各步就绪计数: 每步能生成多少行, 排除了多少。

坏价判定 = 同一 product_code 下 ≥3 个(非占位)已映射 SKU 的报名价完全相同 (各尺寸一个价 = 没按尺寸真实定价)。
样块/样品类白名单 (本就同价, 属正常)。用户一旦把各尺寸价改成真实不同价, 该产品自动不再被判坏价、自动纳入报名。
"""
from __future__ import annotations

import datetime
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

# 同价属正常、不算坏价的类目关键词 (样块/样品统一售价)
_FLAT_PRICE_WHITELIST = ("样块", "样品", "色卡")
# 判定坏价所需的最少同产品 SKU 数 (1~2 个 SKU 的产品无法据"雷同"判断)
_MIN_SKUS_FOR_FLAT_FLAG = 3

# 15 天最低价冲突: 严格口径(2026-07-11 用户拍板: 必须按系统价, 2% 容差太松、丢 2 个点利润不行)。
# 只要「计划大促到手 > 近 15 天真实最低成交」就报; 留 1 分钱浮点保护, 防四舍五入假报。
_CONFLICT_FLOAT_GUARD = 0.01   # 元


def _report_price(promo, params) -> float | None:
    from app.services import pricing_calc_service
    try:
        return pricing_calc_service.report_prices(promo, params).get("report_price")
    except Exception:  # noqa: BLE001
        return None


def bad_price_product_codes(db: Session) -> set[str]:
    """返回被判"坏价"的 product_code 集合 (供报名/单品立减 builder 排除)。

    坏价 = 同产品 ≥3 个非占位已映射 SKU 的报名价完全雷同 (各尺寸没真实定价)。样块类白名单。
    改成各尺寸真实不同价后, 该产品自动移出此集合、自动重新纳入报名。
    """
    return {b["product_code"] for b in _detect_bad_products(db)}


def _detect_bad_products(db: Session) -> list[dict]:
    from app.models.pricing import PricingSku
    from app.models.pricing_ext import PricingSkuPromo
    from app.services import pricing_calc_service

    params = pricing_calc_service.get_promo_params(db)
    skus = db.execute(select(PricingSku)).scalars().all()
    promo_by_sku = {p.sku_code: p for p in db.execute(select(PricingSkuPromo)).scalars().all()}

    by_pc: dict[str, list[tuple]] = defaultdict(list)
    for s in skus:
        if getattr(s, "is_custom_placeholder", False):
            continue
        p = promo_by_sku.get(s.sku_code)
        if p is None or not p.taobao_sku_id:
            continue
        rp = _report_price(p, params)
        if rp is None:
            continue
        by_pc[s.product_code or s.sku_code].append((s, float(rp)))

    bad: list[dict] = []
    for pc, lst in by_pc.items():
        name = lst[0][0].sku or lst[0][0].product_name or pc
        if any(k in (name or "") for k in _FLAT_PRICE_WHITELIST):
            continue
        distinct = {round(rp) for _, rp in lst}
        if len(lst) >= _MIN_SKUS_FOR_FLAT_FLAG and len(distinct) == 1:
            bad.append({
                "product_code": pc,
                "name": name,
                "sku_count": len(lst),
                "report_price": lst[0][1],
                "reason": f"{len(lst)}个尺寸报名价全部={lst[0][1]:.0f}(未按尺寸真实定价)",
            })
    bad.sort(key=lambda b: -b["sku_count"])
    return bad


def activity_preflight(db: Session, floor_days: int = 15, skip_floor_check: bool = False) -> dict:
    """活动报名虚拟推送(预检)。只读, 返回问题清单 + 各步就绪计数。
    skip_floor_check=True: 本次按【初始报价】跳过 15 天最低价冲突校验(首次立基准), 未来仍应照跑。"""
    from app.models.order import Order
    from app.models.pricing import PricingSku
    from app.models.pricing_ext import PricingSkuPromo
    from app.services import pricing_calc_service

    params = pricing_calc_service.get_promo_params(db)
    skus = db.execute(select(PricingSku)).scalars().all()
    promo_by_sku = {p.sku_code: p for p in db.execute(select(PricingSkuPromo)).scalars().all()}

    bad = _detect_bad_products(db)
    bad_pc = {b["product_code"] for b in bad}

    # 2) 缺淘宝映射: 有日常价的非占位 SKU 却没 taobao_sku_id
    unmapped_by_pc: dict[str, int] = defaultdict(int)
    unmapped_total = 0
    for s in skus:
        if getattr(s, "is_custom_placeholder", False) or s.daily_price is None:
            continue
        p = promo_by_sku.get(s.sku_code)
        if p is None or not p.taobao_sku_id:
            unmapped_total += 1
            unmapped_by_pc[s.product_code or s.sku_code] += 1

    # 3) 15 天最低价冲突: 计划大促到手 = big_buyer_price; 近 floor_days 最低实付(真实淘宝订单)。
    #    初始报价可整体跳过(skip_floor_check=True): 首次以活动价立基准, 不拿零散成交当涨价线; 未来仍照跑 (2026-07-11 用户)。
    conflicts: list[dict] = []
    if not skip_floor_check:
        since = datetime.date.today() - datetime.timedelta(days=floor_days)
        rows = db.execute(
            select(Order.sku_code, func.min(Order.paid_amount))
            .where(Order.order_date >= since, Order.platform == "淘宝",
                   Order.is_refill.is_(False), Order.qty == 1,
                   Order.paid_amount.isnot(None), Order.paid_amount > 50,
                   Order.sku_code.isnot(None), Order.status.notin_(("cancelled",)))
            .group_by(Order.sku_code)).all()
        paidmin = {c: float(m) for c, m in rows if c and m is not None}
        for s in skus:
            if getattr(s, "is_custom_placeholder", False):
                continue
            p = promo_by_sku.get(s.sku_code)
            if p is None or not p.taobao_sku_id or p.big_buyer_price is None:
                continue
            fl = paidmin.get(s.sku_code)
            if fl is None:
                continue
            planned = float(p.big_buyer_price)
            # 严格: 计划大促到手 只要高过近 15 天真实最低成交 1 分钱以上即报(必须按系统价, 不放水)。
            gap_pct = (planned - fl) / fl * 100 if fl else 0
            if planned - fl > _CONFLICT_FLOAT_GUARD:
                conflicts.append({
                    "sku_code": s.sku_code,
                    "name": s.sku or s.product_name or s.sku_code,
                    "planned_shoudao": round(planned, 2),   # 计划大促到手 = 报名价A × 0.88
                    "recent_min_paid": round(fl, 2),         # 近 floor_days 真实最低成交
                    "gap_pct": round(gap_pct, 2),
                })
        conflicts.sort(key=lambda x: -x["gap_pct"])

    # 4) 各步就绪计数 (排除坏价产品后, 能生成多少行)
    def _emit_count(price_field: str, placeholder_mul: float) -> dict:
        rows_n = skipped_bad = skipped_no_price = 0
        for s in skus:
            p = promo_by_sku.get(s.sku_code)
            if p is None or not p.taobao_item_id or not p.taobao_sku_id:
                continue
            if (s.product_code or "") in bad_pc:
                skipped_bad += 1
                continue
            if getattr(s, "is_custom_placeholder", False):
                price = float(s.daily_price) * placeholder_mul if s.daily_price else None
            else:
                price = pricing_calc_service.report_prices(p, params).get(price_field)
            if price is None:
                skipped_no_price += 1
                continue
            rows_n += 1 + len([a for a in (p.alt_taobao_sku_ids or []) if a])
        return {"rows": rows_n, "skipped_bad_price": skipped_bad,
                "skipped_no_price": skipped_no_price}

    return {
        "floor_days": floor_days,
        "bad_products": bad,
        "bad_product_count": len(bad),
        "bad_sku_count": sum(b["sku_count"] for b in bad),
        "unmapped_total": unmapped_total,
        "unmapped_by_product": dict(sorted(unmapped_by_pc.items(), key=lambda x: -x[1])[:20]),
        "conflict_count": len(conflicts),
        "conflicts": conflicts[:100],
        "floor_check_skipped": skip_floor_check,   # 本次是否按初始报价跳过了15天校验
        "signup_big": _emit_count("report_price", 0.9),       # 88VIP大促12% / 超级立减10% 同报名价
        "signup_618": _emit_count("report_price_618", 0.9),   # 超级大促15% (换SKU)
    }
