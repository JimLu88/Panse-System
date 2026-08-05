"""活动报名「虚拟推送(预检)」—— 生成活动表【前】的体检 (2026-07-11 用户需求)。

不产文件、不改数据、不上传淘宝。把"如果现在生成三步活动表并上传会出什么问题"提前算出来:
  1. 坏价产品   : 各尺寸报名价雷同(占位价/复制价未真实定价), 会把废价带上淘宝 → 排除并提示先改价;
  2. 缺淘宝映射 : 有日常价但没 taobao_sku_id 的 SKU, 报名表里不会出现 → 淘宝报名会报"缺SKU";
  3. 平台资格冲突: 报名价高于最低标价，或报名价−官方立减高于最低普惠券后价 → 整品暂缓;
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


# 档1 商品价格核验: 0.75 = 系统标准单品宝折扣(永不改), 千牛一口价 应 = ERP日常价 ÷ 0.75。
_STANDARD_PROMO_DISCOUNT = 0.75
_PRICE_CHECK_REL_TOL = 0.01   # 1% 相对容差(min 1元), 防四舍五入噪声误报


def product_price_check(db: Session) -> dict:
    """档1 商品价格核验 (2026-07-13 用户「ERP价为准」铁律): ERP日常价 vs 千牛标价(快照)。

    千牛价取自最近一次「淘宝商品导出」导入的 TaobaoListing(按 sku_code 关联, 取 sku_price, 缺则 list_price)。
    铁律: 千牛一口价 应 = ERP日常价 ÷ 0.75。偏低的(千牛价 < 应填价)= 单品宝把标价卡在低值 < ERP日常价 →
    超级立减/报名会被平台判"标价高于近15天最低" 拒 → 需去千牛把一口价抬到 应填价。
    快照里没有的 SKU 不判(没导出/没匹配, 避免误报)。返回档1明细 + 快照日期。"""
    from app.models.pricing import PricingSku
    from app.models.pricing_ext import PricingSkuPromo
    from app.models.taobao_listing import TaobaoListing

    skus = db.execute(select(PricingSku)).scalars().all()
    promo_by_sku = {p.sku_code: p for p in db.execute(select(PricingSkuPromo)).scalars().all()}

    qn_price: dict[str, float] = {}
    snapshot: datetime.datetime | None = None
    for L in db.execute(select(TaobaoListing)).scalars().all():
        code = (L.sku_code or L.merchant_code or "").strip()
        px = L.sku_price if L.sku_price is not None else L.list_price
        if code and px is not None:
            qn_price[code] = float(px)
        ts = getattr(L, "updated_at", None) or getattr(L, "created_at", None)
        if ts and (snapshot is None or ts > snapshot):
            snapshot = ts

    mismatches: list[dict] = []
    checked = ok = 0
    for s in skus:
        if getattr(s, "is_custom_placeholder", False) or s.daily_price is None:
            continue
        p = promo_by_sku.get(s.sku_code)
        if p is None or not p.taobao_sku_id:
            continue
        qn = qn_price.get(s.sku_code)
        if qn is None:
            continue
        checked += 1
        expected = round(float(s.daily_price) / _STANDARD_PROMO_DISCOUNT, 2)
        if abs(qn - expected) <= max(1.0, expected * _PRICE_CHECK_REL_TOL):
            ok += 1
            continue
        mismatches.append({
            "sku_code": s.sku_code,
            "name": s.sku or s.product_name or s.sku_code,
            "erp_daily": round(float(s.daily_price), 2),
            "qn_price": round(qn, 2),                 # 现千牛标价(快照)
            "expected_qn": expected,                  # 应改一口价 = 日常价 ÷ 0.75
            "diff": round(qn - expected, 2),
            "too_low": qn < expected,                 # True=千牛偏低(阻塞报名, 急)
        })
    mismatches.sort(key=lambda m: (not m["too_low"], m["diff"]))   # 偏低的排前
    return {
        "price_checked": checked,
        "price_ok": ok,
        "price_mismatch_count": len(mismatches),
        "price_too_low_count": sum(1 for m in mismatches if m["too_low"]),
        "price_mismatches": mismatches[:100],
        "price_snapshot_date": snapshot.date().isoformat() if snapshot else None,
        "price_has_snapshot": bool(qn_price),
    }


def activity_preflight(db: Session, floor_days: int = 15, skip_floor_check: bool = False,
                       tier: str = "big") -> dict:
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
                price = float(s.daily_price) if s.daily_price is not None else None
            if price is None:
                skipped_no_price += 1
                continue
            rows_n += 1 + len([a for a in (p.alt_taobao_sku_ids or []) if a])
        return {"rows": rows_n, "skipped_bad_price": skipped_bad,
                "skipped_no_price": skipped_no_price}

    # 6) 报名可行性：最低标价与最低普惠券后价是两个独立资格门。
    #    历史线只决定整品是否暂缓，绝不能用于改写真实 SKU 报名价或单品立减。
    #    b. 整商品不完整: 淘宝要求全SKU报名, 任一已映射SKU算不出价 → collect 已整商品剔除, 这里红字列明;
    #    c. 动销疑似不达标: 近60天销量=0 的商品(上架>60天要求近60天≥1件; 上架时间未知, 故标"疑似")。
    from app.services.data_export_service import collect_signup_rows
    sig_entries, sig_stats = collect_signup_rows(db, "report_price")
    from decimal import Decimal
    from app.services import campaign_price_floor_service, campaign_service

    evidence = campaign_price_floor_service.evidence_map(db)
    lev = campaign_service.TIER_LEVERAGE.get(tier, campaign_service.TIER_LEVERAGE["big"])
    ceil_on = campaign_service.official_ceil_enabled(db) if tier == "mid" else True
    floor_conflicts: list[dict] = []
    floor_evidence_missing: list[dict] = []
    for s, p, A in sig_entries:
        low_price_exact = Decimal(str(A)) < Decimal("100")
        official = campaign_service.official_deduction(
            Decimal(str(A)), lev, ceil_on and not low_price_exact)
        coupon_after = Decimal(str(A)) - official
        for sid in [p.taobao_sku_id, *(p.alt_taobao_sku_ids or [])]:
            sid = str(sid or "").strip()
            if not sid:
                continue
            entry = evidence.get(sid) if isinstance(evidence.get(sid), dict) else {}
            missing = [key for key in ("min_list_price", "min_coupon_line")
                       if entry.get(key) is None]
            if missing:
                floor_evidence_missing.append({
                    "sku_code": s.sku_code,
                    "taobao_sku_id": sid,
                    "missing": missing,
                    "observed_at": entry.get("observed_at"),
                })
                continue
            min_list = Decimal(str(entry["min_list_price"]))
            min_coupon = Decimal(str(entry["min_coupon_line"]))
            reasons = []
            if Decimal(str(A)) > min_list + Decimal("0.005"):
                reasons.append({"type": "minimum_list_price", "line": float(min_list)})
            if coupon_after > min_coupon + Decimal("0.005"):
                reasons.append({
                    "type": "minimum_coupon_after_price",
                    "line": float(min_coupon),
                    "qualification_coupon_after": float(coupon_after),
                    "single_item_discount_counts": False,
                })
            if reasons:
                floor_conflicts.append({
                    "sku_code": s.sku_code,
                    "taobao_sku_id": sid,
                    "name": s.sku or s.product_name or s.sku_code,
                    "signup_price": round(A, 2),
                    "official_deduction": float(official),
                    "reasons": reasons,
                    "source": entry.get("source"),
                    "observed_at": entry.get("observed_at"),
                })
    floor_conflicts.sort(key=lambda x: (x["sku_code"], x["taobao_sku_id"]))
    since60 = datetime.date.today() - datetime.timedelta(days=60)
    sold = {c: float(q or 0) for c, q in db.execute(
        select(Order.sku_code, func.sum(Order.qty))
        .where(Order.order_date >= since60, Order.platform == "淘宝",
               Order.is_refill.is_(False), Order.sku_code.isnot(None),
               Order.status.notin_(("cancelled",)))
        .group_by(Order.sku_code)).all()}
    item_sales: dict[str, float] = defaultdict(float)
    item_name: dict[str, str] = {}
    for s, p, _A in sig_entries:
        iid = str(p.taobao_item_id)
        item_sales[iid] += sold.get(s.sku_code, 0)
        item_name.setdefault(iid, (s.product_name or s.product_code or "")[:30])
    no_sales_items = [{"taobao_item_id": iid, "product": item_name[iid]}
                      for iid, q in sorted(item_sales.items()) if q <= 0]

    # 5) 淘宝SKUID撞号: 一个 SKUID 被多个商家编码共用(主或alt) → 上传表两行打架、后行覆盖前行 = 必串价。
    #    在生成上传表【之前】暴露串号 (用户 2026-07-12; 例: 6042972321593 被 小横隔板/竖隔板 共用,
    #    比对表事后逮到 64.13≠92.31)。只读检测。
    sid_owners: dict[str, set] = defaultdict(set)
    for p in promo_by_sku.values():
        for _sid in [p.taobao_sku_id, *(getattr(p, "alt_taobao_sku_ids", None) or [])]:
            _s = str(_sid).strip() if _sid else ""
            if _s:
                sid_owners[_s].add(p.sku_code)
    name_by_code = {s.sku_code: (s.sku or s.product_name or "") for s in skus}
    skuid_collisions = [
        {"taobao_sku_id": sid,
         "sku_codes": sorted(codes),
         "names": [f"{c}（{name_by_code.get(c, '?')}）" for c in sorted(codes)]}
        for sid, codes in sid_owners.items() if len(codes) > 1
    ]
    skuid_collisions.sort(key=lambda x: x["taobao_sku_id"])

    return {
        "tier": tier,
        **product_price_check(db),                            # 档1 商品价格核验(ERP日常价 vs 千牛标价快照)
        "floor_days": floor_days,
        "bad_products": bad,
        "bad_product_count": len(bad),
        "bad_sku_count": sum(b["sku_count"] for b in bad),
        "unmapped_total": unmapped_total,
        "unmapped_by_product": dict(sorted(unmapped_by_pc.items(), key=lambda x: -x[1])[:20]),
        "conflict_count": len(conflicts),
        "conflicts": conflicts[:100],
        "floor_check_skipped": skip_floor_check,   # 本次是否按初始报价跳过了15天校验
        "skuid_collision_count": len(skuid_collisions),
        "skuid_collisions": skuid_collisions[:50],
        "floor_conflict_count": len(floor_conflicts),
        "floor_conflicts": floor_conflicts[:100],
        "floor_evidence_missing_count": len(floor_evidence_missing),
        "floor_evidence_missing": floor_evidence_missing[:100],
        "incomplete_item_count": sig_stats["skipped_incomplete_items"],   # 整商品不完整(缺价SKU) → 已剔除
        "incomplete_items": sig_stats["incomplete_items"][:50],
        "no_sales_count": len(no_sales_items),                 # 近60天0销量(疑似动销不达标, 警示)
        "no_sales_items": no_sales_items[:50],
        "signup_big": _emit_count("report_price", 0.9),       # 88VIP大促12% / 超级立减10% 同报名价
        "signup_618": _emit_count("report_price_618", 0.9),   # 超级大促15% (换SKU)
    }
