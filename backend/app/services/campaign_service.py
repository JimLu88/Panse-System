"""活动生命周期 P1 引擎 (2026-07-17 权威 spec: docs/活动生命周期系统_执行plan.md)。

职责:
- group_by_sales      动销检查与分组 (spec §四.1) + no_sales 登记表同步
- build_signup_rows   报名行 builder: 报名价=日常价 / 占位=min(现行, floor(线/(1−lev))) (spec §二.1, R3/R4)
- build_discount_rows 单品立减 builder: spec §二 立减公式逐字 (官方立减向上取整到元 R9 /
                      贴线 min(目标,线) R2 / 无动销=日常−(中促+1) / 10% ceil 留开关)
- preflight           平台规则库 R1~R12 静态可查项逐条输出 (spec §三)
- push_discount/push_signup  推送编排 (复用 web_agent_service upload_file→wait_job,
                      与 activity_upload_service 同模式)
- target_prices       核对器用的逐 skuId 目标到手 (campaign_recon_service 消费)

铁则 (spec §二, 用户 2026-07-17 拍板):
  报名价 = ERP 日常价, 永不再变; 中促 = 大促 × 1.03 (就地计算, 不写 mid_buyer 字段——那是任务#22);
  无动销到手 = 中促 + 1 (防零头撞线); ERP 价是唯一标准。
只读 PricingSku / PricingSkuPromo, 绝不改其字段。
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

# 活动类型 → (人话名, 档位)。档位: mid=超级立减10%→中促到手 / big=12%→大促到手 / big618=15%→大促到手
CAMPAIGN_TYPES = {
    "super_reduce": ("超级立减", "mid"),
    "big88":        ("88VIP大促", "big"),
    "big38":        ("38大促", "big"),
    "big_other":    ("其他大促", "big"),
    "big618":       ("618大促", "big618"),
    "big11":        ("双11大促", "big618"),
}
TIER_LEVERAGE = {"mid": Decimal("0.10"), "big": Decimal("0.12"), "big618": Decimal("0.15")}
MID_OVER_BIG_RATIO = Decimal("1.03")       # 任务#22: 中促 = 大促 × 1.03 (系统统一系数, 就地算)
NOSALES_MARKUP_YUAN = Decimal("1")         # 无动销: 到手 = 中促 + 1 元 (2026-07-17 永久规则)
LINE_CONCESSION_MAX_YUAN = Decimal("1")    # 贴线让幅 > 1 元 → 建议轮换而非贴线 (R2)
PLACEHOLDER_LINE_FALLBACK_RATIO = Decimal("0.8")   # 占位无券后线 → 日常×0.8 保守线(行备注标注)
OFFICIAL_CEIL_KEY = "campaign_official_ceil"       # 10% 官方立减是否向上取整(待7-20实证), 默认真
_CENT = Decimal("0.01")
# spec §四.1 说剔 closed; 本库订单状态机把交易关闭存成 cancelled → 两个都剔 (口径决定, 见交付说明)
_CLOSED_STATUSES = ("closed", "cancelled")


def _d(x) -> Optional[Decimal]:
    if x is None:
        return None
    try:
        return Decimal(str(x))
    except Exception:  # noqa: BLE001 — 脏数据当缺失, 不炸主流程
        return None


def plan_tier(plan) -> str:
    """计划档位: 优先已固化的 plan.tier, 缺省由活动类型派生。未知类型显式报错。"""
    tier = getattr(plan, "tier", None)
    if tier in TIER_LEVERAGE:
        return tier
    ctype = getattr(plan, "campaign_type", None)
    if ctype not in CAMPAIGN_TYPES:
        raise ValueError(f"未知活动类型 {ctype!r}; 可选 {list(CAMPAIGN_TYPES)}")
    return CAMPAIGN_TYPES[ctype][1]


def official_ceil_enabled(db: Session) -> bool:
    """超级立减10% 官方立减是否向上取整到元 (spec §二: 待 7-20 实证, 默认按取整)。"""
    from app.services import settings_service
    raw = settings_service.get(db, OFFICIAL_CEIL_KEY, env_fallback=False)
    if raw is None or str(raw).strip() == "":
        return True
    return str(raw).strip().lower() not in ("0", "false", "off", "no")


def official_deduction(daily: Decimal, lev: Decimal, ceil_on: bool = True) -> Decimal:
    """官方立减金额 = 日常价 × 场次力度, 向上取整到元 (R9 平台实测 339.3→340、260.1→261)。
    ceil_on=False (仅 10% 场留的开关) → 精确到分不取整。"""
    exact = daily * lev
    if ceil_on:
        return exact.to_integral_value(rounding=ROUND_CEILING)
    return exact.quantize(_CENT, ROUND_HALF_UP)


def mid_buyer_inplace(promo) -> Optional[Decimal]:
    """中促到手 = 大促到手 × 1.03, 就地计算 (任务#22 口径; 不读不写 mid_buyer_price 字段本身)。"""
    big = _d(getattr(promo, "big_buyer_price", None))
    if big is None or big <= 0:
        return None
    return (big * MID_OVER_BIG_RATIO).quantize(_CENT, ROUND_HALF_UP)


def _expand_sku_ids(promo) -> list[str]:
    """一码多SKU: [主SKUID, *alt] 去重去空 (与 activity_upload_service._expand_ids 同口径)。"""
    ids: list[str] = []
    for sid in [promo.taobao_sku_id, *(promo.alt_taobao_sku_ids or [])]:
        s = str(sid).strip() if sid else ""
        if s and s not in ids:
            ids.append(s)
    return ids


def _mapped_pairs(db: Session) -> list[tuple]:
    """全部已映射 (PricingSku, PricingSkuPromo) 对, 按 product_code/sku_code 稳定排序。"""
    from app.models.pricing import PricingSku
    from app.models.pricing_ext import PricingSkuPromo
    skus = db.execute(select(PricingSku).order_by(
        PricingSku.product_code, PricingSku.sku_code)).scalars().all()
    promo_by_sku = {p.sku_code: p for p in db.execute(select(PricingSkuPromo)).scalars().all()}
    out = []
    for s in skus:
        p = promo_by_sku.get(s.sku_code)
        if p is not None and p.taobao_item_id:
            out.append((s, p))
    return out


# ── 1. 动销检查与分组 (spec §四.1) ─────────────────────────────────────────────

def group_by_sales(db: Session, days: int = 60) -> dict:
    """近{days}天淘宝订单(剔关闭单, 含刷单=平台视角)按 product_code→taobao_item_id 聚合
    → {有动销/无动销}; 并与 no_sales_service 登记表同步: 新零动销自动登记;
    已登记但出了单的只进 promote_candidates 提示转正, **不自动移除** (R6 单行道)。"""
    from datetime import date, timedelta
    from app.models.order import Order
    from app.services import no_sales_service
    from app.services.product_coder import brand_variants

    cutoff = date.today() - timedelta(days=days)
    sold_rows = db.execute(select(Order.product_code).where(
        Order.platform == "淘宝",
        Order.order_date >= cutoff,
        Order.product_code.isnot(None),
        Order.status.notin_(_CLOSED_STATUSES),
    )).scalars().all()
    sold_codes: set[str] = set()
    for pc in sold_rows:
        sold_codes |= brand_variants(pc)

    item_codes: dict[str, set] = defaultdict(set)
    item_names: dict[str, str] = {}
    for s, p in _mapped_pairs(db):
        iid = str(p.taobao_item_id).strip()
        item_codes[iid].add(s.product_code or "")
        item_names.setdefault(iid, s.product_name or s.product_code or "")
    active, inactive = [], []
    for iid in sorted(item_codes):
        has_sale = any(brand_variants(c) & sold_codes for c in item_codes[iid] if c)
        (active if has_sale else inactive).append(iid)

    registered = no_sales_service.get_no_sales(db)
    newly = sorted(set(inactive) - registered)
    if newly:
        no_sales_service.add_no_sales(db, newly)     # 新零动销自动登记
    promote = sorted(registered & set(active))       # 出单了 → 提示转正, 不自动移除
    return {"有动销": active, "无动销": inactive, "days": days,
            "newly_registered": newly, "promote_candidates": promote,
            "registered": sorted(no_sales_service.get_no_sales(db)),
            "item_names": item_names}


def no_sales_export_rows(db: Session, days: int = 60) -> list[dict]:
    """无动销名单导出行 (spec §四.2a 一键导出): 每个无动销淘宝商品一行
    {product_name, product_codes, taobao_item_id, sales_60d, action}。
    促成交名单以「无动销组」为准 (与飞书推送同口径); 已出单的登记品 (promote_candidates)
    也带上并标「建议转正」, 运营一张表看全。"""
    from collections import Counter
    from datetime import date, timedelta
    from app.models.order import Order
    from app.services.product_coder import brand_variants

    grouping = group_by_sales(db, days)
    cutoff = date.today() - timedelta(days=days)
    sold_rows = db.execute(select(Order.product_code).where(
        Order.platform == "淘宝",
        Order.order_date >= cutoff,
        Order.product_code.isnot(None),
        Order.status.notin_(_CLOSED_STATUSES),
    )).scalars().all()
    counts: Counter = Counter(sold_rows)

    item_codes: dict[str, set] = defaultdict(set)
    for s, p in _mapped_pairs(db):
        item_codes[str(p.taobao_item_id).strip()].add(s.product_code or "")

    def _sales_of(iid: str) -> int:
        variants: set[str] = set()
        for c in item_codes.get(iid, set()):
            if c:
                variants |= brand_variants(c)
        return sum(n for pc, n in counts.items() if pc and (brand_variants(pc) & variants))

    names = grouping["item_names"]
    rows: list[dict] = []
    for iid in grouping["无动销"]:
        rows.append({
            "product_name": names.get(iid, ""),
            "product_codes": "、".join(sorted(c for c in item_codes.get(iid, set()) if c)),
            "taobao_item_id": iid,
            "sales_60d": _sales_of(iid),
            "action": "促成交; 到手=中促+1; 勿撤在场报名(动销门单行道 R6)",
        })
    for iid in grouping["promote_candidates"]:
        rows.append({
            "product_name": names.get(iid, ""),
            "product_codes": "、".join(sorted(c for c in item_codes.get(iid, set()) if c)),
            "taobao_item_id": iid,
            "sales_60d": _sales_of(iid),
            "action": "已出单→建议转正: 撤 nosales 立减 → 报名大促",
        })
    return rows


# ── 2. 报名行 builder (spec §二.1, R3/R4) ─────────────────────────────────────

def _placeholder_signup_price(s, p, lev: Decimal) -> tuple[Optional[float], Optional[str]]:
    """占位SKU报名价 = min(现行值, floor(线/(1−lev)))。
    现行值 = 日常×0.9 → 500 顶 → enrolled_floor 封顶 (与现网占位口径一致);
    线 = coupon_floor_price; 无线 → 日常×0.8 保守值并在行备注标注。返回 (price|None, remark|None)。"""
    daily = _d(s.daily_price)
    if daily is None or daily <= 0:
        return None, None
    current = min((daily * Decimal("0.9")).quantize(_CENT, ROUND_HALF_UP), Decimal("500"))
    fl = _d(getattr(p, "enrolled_floor_price", None))
    if fl is not None and fl > 0:
        current = min(current, fl.quantize(_CENT, ROUND_HALF_UP))
    line = _d(getattr(p, "coupon_floor_price", None))
    remark = None
    if line is None or line <= 0:
        line = daily * PLACEHOLDER_LINE_FALLBACK_RATIO
        remark = "无券后线, 按日常价×0.8保守值封顶"
    cap = (line / (Decimal("1") - lev)).to_integral_value(rounding=ROUND_FLOOR)
    return float(min(current, cap)), remark


def signup_price_for_sku(s, p, tier: str) -> tuple[Optional[float], Optional[str]]:
    """单个 SKU 的活动报名价唯一口径。

    真 SKU 永远填 ERP 日常价；定制占位 SKU 才走既有保护价。这个函数同时供自动报名、
    定价页和下载表调用，避免各入口再次自行反推报名价。
    """
    if tier not in TIER_LEVERAGE:
        raise ValueError(f"未知活动档位 {tier!r}; 可选 {list(TIER_LEVERAGE)}")
    if bool(getattr(s, "is_custom_placeholder", False)):
        return _placeholder_signup_price(s, p, TIER_LEVERAGE[tier])
    daily = _d(getattr(s, "daily_price", None))
    return (float(daily), None) if daily is not None and daily > 0 else (None, None)


def _item_signup_rows(item_id: str, pairs: list, lev: Decimal, stats: dict) -> tuple[list, list]:
    """单商品报名行收集: 返回 (rows, missing_sku_codes)。真SKU 报名价 = 日常价 (铁则1)。"""
    rows, missing = [], []
    for s, p in pairs:
        placeholder = bool(getattr(s, "is_custom_placeholder", False))
        tier = next(k for k, v in TIER_LEVERAGE.items() if v == lev)
        price, remark = signup_price_for_sku(s, p, tier)
        if placeholder:
            if remark:
                stats["placeholder_no_line"].append({"sku_code": s.sku_code, "remark": remark})
        if price is None or price <= 0:
            missing.append(f"{s.sku_code}（{s.sku or s.product_name or '?'}）")
            continue
        for sid in _expand_sku_ids(p):
            rows.append({"taobao_item_id": item_id, "taobao_sku_id": sid,
                         "sku_code": s.sku_code, "price": round(price, 2),
                         "is_placeholder": placeholder, "remark": remark})
    return rows, missing


def build_signup_rows(db: Session, plan) -> tuple[list[dict], dict]:
    """报名行 builder: 报名价=日常价; 过滤下架(R4)+坏价; 整品全SKU完整性断言(R3):
    任一在售已映射SKU算不出价 → 整品剔除并记 incomplete_items (半套必拒, 绝不静默)。
    返回 (rows, stats); 行 = {taobao_item_id, taobao_sku_id, sku_code, price, is_placeholder, remark}。"""
    from app.services import delisted_sku_service, no_sales_service
    from app.services.activity_preflight_service import bad_price_product_codes

    lev = TIER_LEVERAGE[plan_tier(plan)]
    delisted = delisted_sku_service.get_delisted(db)
    registered_no_sales = no_sales_service.get_no_sales(db)
    bad_pc = bad_price_product_codes(db)
    stats = {"rows": 0, "skipped_no_skuid": 0, "skipped_delisted": 0,
             "skipped_bad_price": 0,
             "skipped_bad_price_items": [], "incomplete_items": [], "placeholder_no_line": []}
    stats["excluded_no_sales_items"] = []
    by_item: dict[str, list] = defaultdict(list)
    for s, p in _mapped_pairs(db):
        if not p.taobao_sku_id:
            stats["skipped_no_skuid"] += 1
            continue
        if str(p.taobao_sku_id) in delisted:          # R4: 下架SKU不出报名行 (不进完整性统计)
            stats["skipped_delisted"] += 1
            continue
        by_item[str(p.taobao_item_id).strip()].append((s, p))

    rows: list[dict] = []
    for item_id, pairs in sorted(by_item.items()):
        # 无动销登记是单行道：真无动销及“已出单待人工转正”都不能自动报名，
        # 只走同期单品立减。每日自动任务会先刷新登记表。
        if item_id in registered_no_sales:
            stats["excluded_no_sales_items"].append(item_id)
            continue
        if all((s.product_code or "") in bad_pc for s, _ in pairs):
            stats["skipped_bad_price_items"].append(item_id)      # 坏价整品排除
            stats["skipped_bad_price"] += len(pairs)
            continue
        item_rows, missing = _item_signup_rows(item_id, pairs, lev, stats)
        if missing:                                    # R3 整品完整性: 缺一个SKU=整品拒 → 整品剔除
            stats["incomplete_items"].append({
                "taobao_item_id": item_id,
                "product": (pairs[0][0].product_name or pairs[0][0].product_code or "")[:30],
                "ok_skus": len(item_rows), "missing_skus": missing[:10]})
            continue
        rows.extend(item_rows)
    stats["rows"] = len(rows)
    return rows, stats


# ── 3. 单品立减 builder (spec §二 立减公式逐字) ────────────────────────────────

def _campaign_discount_row(s, p, tier: str, lev: Decimal, ceil_on: bool, stats: dict) -> Optional[dict]:
    """有动销 SKU 立减: 立减 = 日常 − 官方立减(ceil到元) − min(目标到手, 线)。
    贴线让幅 0~1 元记录在案 (audit); >1 元 → 不贴线, 剔除并建议轮换 (R2)。"""
    daily = _d(s.daily_price)
    target0 = mid_buyer_inplace(p) if tier == "mid" else _d(getattr(p, "big_buyer_price", None))
    if target0 is None or target0 <= 0:
        stats["skipped_no_target"] += 1
        return None
    line = _d(getattr(p, "coupon_floor_price", None))
    target, concession = target0, Decimal("0")
    if line is not None and 0 < line < target0:
        concession = (target0 - line).quantize(_CENT)
        if concession > LINE_CONCESSION_MAX_YUAN:
            stats["rotation_suggested"].append({          # R2: 让幅>1元 → 轮换而非贴线
                "sku_code": s.sku_code, "target": float(target0),
                "line": float(line), "concession": float(concession)})
            return None
        target = line                                     # 贴线 min(目标, 线)
        stats["line_concessions"].append({"sku_code": s.sku_code, "target": float(target0),
                                          "line": float(line), "concession": float(concession)})
    # 2026-07-24 平台实证：低价 SKU 的官方立减按精确比例计算到分。
    # 狂暑季 ¥30×12%=¥3.60；超级立减 ¥25×10%=¥2.50，均不向上取整到元。
    # 普通价位仍沿用整元向上取整规则。
    low_price_exact = daily < Decimal("100")
    official = official_deduction(daily, lev, ceil_on and not low_price_exact)
    if low_price_exact:
        stats["official_low_price_exact"] += 1
    deduct = (daily - official - target).quantize(_CENT)
    if deduct <= 0:
        stats["skipped_no_deduct"] += 1                   # 官方立减已够 → 不出行(不给假数)
        return None
    return {"deduct": float(deduct), "kind": "campaign", "target_price": float(target),
            "official": float(official), "concession": float(concession)}


def _nosales_discount_row(s, p, stats: dict, tier: str = "mid") -> Optional[dict]:
    """无动销 SKU：不报名活动，只靠单品立减直达到当前场次目标。

    大促档位直接到 ERP 大促买家价；超级立减档位沿用中促+1 的保护规则。
    两者都没有官方立减项，也不套券后线。
    """
    daily = _d(s.daily_price)
    if tier in ("big", "big618"):
        target = _d(getattr(p, "big_buyer_price", None))
    else:
        mid = mid_buyer_inplace(p)
        target = ((mid + NOSALES_MARKUP_YUAN).quantize(_CENT)
                  if mid is not None else None)
    if target is None or target <= 0:
        stats["skipped_no_target"] += 1
        return None
    deduct = (daily - target).quantize(_CENT)
    if deduct <= 0:
        stats["skipped_no_deduct"] += 1
        return None
    return {"deduct": float(deduct), "kind": "nosales", "target_price": float(target),
            "official": 0.0, "concession": 0.0}


def discount_for_sku(db: Session, s, p, tier: str,
                     no_sales_items: Optional[set[str]] = None) -> Optional[dict]:
    """单个真 SKU 的单品立减计算口径，供下载参考表复用。

    自动上传仍由 build_discount_rows 负责映射、下架、坏价等资格过滤；本函数只负责价格数学。
    """
    if tier not in TIER_LEVERAGE:
        raise ValueError(f"未知活动档位 {tier!r}; 可选 {list(TIER_LEVERAGE)}")
    if bool(getattr(s, "is_custom_placeholder", False)):
        return None
    daily = _d(getattr(s, "daily_price", None))
    if daily is None or daily <= 0:
        return None
    from app.services import no_sales_service
    stats = {"skipped_no_target": 0, "skipped_no_deduct": 0,
             "rotation_suggested": [], "line_concessions": [],
             "official_low_price_exact": 0}
    item_id = str(getattr(p, "taobao_item_id", "") or "").strip()
    nosales = no_sales_items if no_sales_items is not None else no_sales_service.get_no_sales(db)
    if item_id and item_id in nosales:
        return _nosales_discount_row(s, p, stats, tier)
    lev = TIER_LEVERAGE[tier]
    ceil_on = True if tier != "mid" else official_ceil_enabled(db)
    return _campaign_discount_row(s, p, tier, lev, ceil_on, stats)


def build_discount_rows(db: Session, plan) -> tuple[list[dict], dict]:
    """单品立减 builder (spec §二):
      大促12% / 618双11 15%: 立减 = 日常 − ceil(日常×lev) − min(大促到手, 线)
      超级立减10%:            立减 = 日常 − ceil(日常×10%) − min(中促到手, 线)  (ceil 开关默认真)
      无动销(登记表):         立减 = 日常 − (中促 + 1), 占位不出行
    返回 (rows, stats); 行含 taobao_item_id/taobao_sku_id/sku_code/deduct/target_price/kind。"""
    from app.services import delisted_sku_service, no_sales_service
    from app.services.activity_preflight_service import bad_price_product_codes

    tier = plan_tier(plan)
    lev = TIER_LEVERAGE[tier]
    ceil_on = True if lev != TIER_LEVERAGE["mid"] else official_ceil_enabled(db)
    nosales = no_sales_service.get_no_sales(db)
    delisted = delisted_sku_service.get_delisted(db)
    bad_pc = bad_price_product_codes(db)
    stats = {"tier": tier, "official_ceil": ceil_on, "rows": 0, "skipped_no_skuid": 0,
             "skipped_delisted": 0, "skipped_bad_price": 0, "skipped_placeholder": 0,
             "skipped_no_daily": 0, "skipped_no_target": 0, "skipped_no_deduct": 0,
             "line_concessions": [], "rotation_suggested": [],
             "official_low_price_exact": 0}
    rows: list[dict] = []
    for s, p in _mapped_pairs(db):
        if not p.taobao_sku_id:
            stats["skipped_no_skuid"] += 1
            continue
        if str(p.taobao_sku_id) in delisted:
            stats["skipped_delisted"] += 1
            continue
        if (s.product_code or "") in bad_pc:
            stats["skipped_bad_price"] += 1
            continue
        if getattr(s, "is_custom_placeholder", False):   # 占位不出行 (spec §二.4)
            stats["skipped_placeholder"] += 1
            continue
        daily = _d(s.daily_price)
        if daily is None or daily <= 0:
            stats["skipped_no_daily"] += 1
            continue
        item_id = str(p.taobao_item_id).strip()
        if item_id in nosales:
            core = _nosales_discount_row(s, p, stats, tier)
        else:
            core = _campaign_discount_row(s, p, tier, lev, ceil_on, stats)
        if core is None:
            continue
        for sid in _expand_sku_ids(p):
            rows.append({"taobao_item_id": item_id, "taobao_sku_id": sid,
                         "sku_code": s.sku_code, **core})
    stats["rows"] = len(rows)
    return rows, stats


# ── 4. preflight (spec §三 R1~R12 静态可查项) ─────────────────────────────────

_STATIC_REMINDERS = [
    ("R5", "warn", "已报名非草稿的品批量导入必被拒 — 推送前 wizard 卡点确认该品已在千牛撤销"),
    ("R7", "info", "轮换核对按 skuId 判定, 不认名字 (同名新建SKU会复活老skuId历史线)"),
    ("R8", "info", "刷新 SKU 映射必须同事务清线 (coupon_floor/enrolled_floor 挂编码、线跟 sid)"),
    ("R10", "info", "回执真相以千牛「批量操作记录」最新一条为准, WA published 回执不可信"),
    ("R11", "warn", "同品同时只能一个单品立减生效 — 推送前先在千牛删除在场旧批, 否则新批不生效"),
    ("R12", "warn", "单品立减导入即生效、报名导入即成功, 均无草稿不可逆 — 每步确认后再推"),
]


def _check_r1(db: Session) -> dict:
    """R1 静态代理: 报名价(=日常价) > 已生效活动价硬底(enrolled_floor_price) → 必被
    "≤近15天最低标价/已生效价"拦, 提示轮换。(真实15天标价窗口在平台侧, 离线取不到 — 交回执自愈。)"""
    items = []
    for s, p in _mapped_pairs(db):
        if getattr(s, "is_custom_placeholder", False):
            continue
        fl = _d(getattr(p, "enrolled_floor_price", None))
        daily = _d(s.daily_price)
        if fl is not None and fl > 0 and daily is not None and daily > fl:
            items.append({"sku_code": s.sku_code, "daily_price": float(daily),
                          "enrolled_floor": float(fl), "advice": "轮换"})
    return {"rule": "R1", "level": "warn" if items else "pass",
            "title": "报名价≤近15天最低标价 (静态代理: 已生效价硬底)", "items": items}


def preflight(db: Session, plan) -> list[dict]:
    """R1~R12 静态可查项逐条输出。每条 {rule, level(pass|info|warn|error), title, items[]}。"""
    from app.services import no_sales_service
    _srows, sstats = build_signup_rows(db, plan)
    _drows, dstats = build_discount_rows(db, plan)
    nosales = sorted(no_sales_service.get_no_sales(db))
    checks = [
        _check_r1(db),
        {"rule": "R2", "level": "error" if dstats["rotation_suggested"] else "pass",
         "title": "券后贴线: 让幅>1元建议轮换而非贴线; 0~1元让幅记录在案",
         "items": dstats["rotation_suggested"], "audit": dstats["line_concessions"]},
        {"rule": "R3", "level": "error" if sstats["incomplete_items"] else "pass",
         "title": "报名整品全SKU完整性 (缺SKU=整品拒)", "items": sstats["incomplete_items"]},
        {"rule": "R4", "level": "info", "title": "下架SKU已过滤不出行 (回执自愈登记)",
         "items": [{"skipped_delisted_signup": sstats["skipped_delisted"],
                    "skipped_delisted_discount": dstats["skipped_delisted"]}]},
        {"rule": "R6", "level": "warn" if nosales else "pass",
         "title": "零动销禁撤名单 (动销门单行道: 撤了回不来, 强制迁移需二次确认)", "items": nosales},
        {"rule": "R9", "level": "pass",
         "title": "官方立减向上取整到元已内建 (10%场开关 campaign_official_ceil)",
         "items": [{"official_ceil": dstats["official_ceil"]}]},
    ]
    checks += [{"rule": r, "level": lv, "title": t, "items": []} for r, lv, t in _STATIC_REMINDERS]
    checks.sort(key=lambda c: int(c["rule"][1:]))
    return checks


# ── 5. 推送编排 (复用 web_agent_service upload_file → wait_job) ────────────────

def _build_discount_xlsx(rows: list[dict]) -> bytes:
    """单品立减上传表 (表头与淘宝模板逐字一致, 复用 data_export_service._TB_DISCOUNT_HEADERS)。"""
    import io
    import openpyxl
    from app.services.data_export_service import _TB_DISCOUNT_HEADERS
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "单品立减"
    for ci, h in enumerate(_TB_DISCOUNT_HEADERS, start=1):
        ws.cell(1, ci, h)
    r, seen = 2, set()
    for row in rows:
        sid = row["taobao_sku_id"]
        if sid in seen:
            continue                                   # 重复映射保首行 (平台"存在重复的SKUID"整品拒)
        seen.add(sid)
        ws.cell(r, 1, str(row["taobao_item_id"])).number_format = "@"
        ws.cell(r, 2, sid).number_format = "@"
        ws.cell(r, 3, float(row["deduct"])).number_format = "0.00"
        r += 1
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _build_signup_xlsx(rows: list[dict]) -> bytes:
    """大促报名上传表: 官方模板 promo_signup_sku.xlsx (保留说明sheet+前3行表头, 数据从第4行)。"""
    import io
    from pathlib import Path
    import openpyxl
    tpl = Path(__file__).resolve().parent.parent / "assets" / "taobao_templates" / "promo_signup_sku.xlsx"
    wb = openpyxl.load_workbook(tpl)
    ws = wb["商品SKU导入列表"]
    if ws.max_row >= 4:                                # 清模板示例数据行, 保留前3行表头
        ws.delete_rows(4, ws.max_row - 3)
    r, seen = 4, set()
    for row in rows:
        sid = row["taobao_sku_id"]
        if sid in seen:
            continue
        seen.add(sid)
        ws.cell(r, 1, str(row["taobao_item_id"])).number_format = "@"
        ws.cell(r, 2, sid).number_format = "@"
        ws.cell(r, 3, float(row["price"])).number_format = "0.00"
        r += 1
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _fmt_dt(dt) -> Optional[str]:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else None


def _plan_campaign_ids(plan) -> tuple[Optional[str], Optional[str]]:
    """从计划备注中读取可选的千牛活动 ID（不新增数据库字段，兼容现有计划表）。"""
    import re
    text = str(getattr(plan, "remark", None) or "")
    cid = re.search(r"(?:campaignId|campaign_id)\s*[:=]\s*(\d+)", text)
    uid = re.search(r"(?:unitedActivityId|united_activity_id)\s*[:=]\s*(\d+)", text)
    return (cid.group(1) if cid else None, uid.group(1) if uid else None)


def _upload_and_wait(db: Session, channel: str, phase: str, xlsx: bytes,
                     start_dt: Optional[str], end_dt: Optional[str], *,
                     plan=None, expected_rows: Optional[int] = None,
                     expected_items: Optional[int] = None) -> dict:
    """WA 上传编排 (与 activity_upload_service 同模式: upload_file → wait_job)。"""
    from app.services import web_agent_service
    extra = {}
    if channel == "promo_signup" and plan is not None:
        title = (getattr(plan, "qn_campaign_title", None)
                 or getattr(plan, "name", None))
        phase_name = (getattr(plan, "name", None)
                      if str(getattr(plan, "name", "")) != str(title or "") else None)
        cid, uid = _plan_campaign_ids(plan)
        extra = {
            "campaign_title": title,
            "campaign_phase": phase_name,
            "campaign_start": start_dt,
            "campaign_end": end_dt,
            "official_rate": f"{int(TIER_LEVERAGE[plan_tier(plan)] * 100)}%",
            "campaign_id": cid,
            "united_activity_id": uid,
        }
    j = web_agent_service.upload_file(
        db, channel, phase, xlsx, f"campaign_{channel}.xlsx",
        start_dt=start_dt, end_dt=end_dt, expected_rows=expected_rows, **extra)
    if not j.get("ok") or not j.get("job"):
        return {"ok": False, "error": j.get("error", "取数服务(:8500)未响应, 无法上传")}
    final = web_agent_service.wait_job(db, j["job"], timeout_s=200)
    res = final.get("result") or {}
    if res.get("need_scan"):
        return {"ok": False, "need_scan": True, "message": "淘宝登录态过期, 请先扫码后再上传"}
    validation = res.get("validation")
    if channel == "promo_signup":
        total = validation.get("total_items") if isinstance(validation, dict) else None
        ok_count = validation.get("ok") if isinstance(validation, dict) else None
        failed = validation.get("failed") if isinstance(validation, dict) else None
        terminal = bool(
            isinstance(total, int) and isinstance(ok_count, int) and isinstance(failed, int)
            and total > 0 and ok_count + failed == total
        )
        success = bool(
            res.get("ok") and terminal and failed == 0 and ok_count == total
            and (expected_items is None or total == expected_items)
        )
        error = res.get("error") or res.get("message")
        if not terminal:
            error = error or "批量操作记录未进入终态（不能把附件已挂上当成报名成功）"
        elif expected_items is not None and total != expected_items:
            error = f"批量操作范围不符：平台{total}品，预期{expected_items}品"
        elif failed:
            error = f"批量操作终态失败：成功{ok_count}品/失败{failed}品"
    else:
        success = bool(res.get("submitted") if phase == "commit" else res.get("ok"))
        error = res.get("error") or res.get("message")
    return {"ok": success, "error": error,
            "job": j["job"], "validation": validation,
            "submitted": res.get("submitted"),
            "screenshot_base64": res.get("screenshot_base64")}


def _learn_from_validation(db: Session, validation) -> None:
    """回执自愈 (尽力而为, 失败不阻断): R4 下架SKU登记 + R6 动销不达标商品登记。"""
    if not validation:
        return
    try:
        from app.services import delisted_sku_service, no_sales_service
        failed = validation.get("failed_items") if isinstance(validation, dict) else None
        ids = delisted_sku_service.extract_delisted_from_feedback(failed)
        if ids:
            delisted_sku_service.add_delisted(db, ids)
        items = no_sales_service.extract_no_sales_from_feedback(failed)
        if items:
            no_sales_service.add_no_sales(db, items)
    except Exception:  # noqa: BLE001 — 自愈失败不影响主流程
        pass


def push_discount(db: Session, plan, phase: str = "stage") -> dict:
    """推单品立减 (channel single_item_discount, 带计划档期精确到秒)。
    phase='stage' 挂文件停在提交前; 'commit' ★不可逆★ 真提交 (仅用户确认后调, R12)。"""
    rows, stats = build_discount_rows(db, plan)
    if not rows:
        return {"ok": False, "error": "无可推送的立减行", "stats": stats}
    res = _upload_and_wait(db, "single_item_discount", phase, _build_discount_xlsx(rows),
                           _fmt_dt(plan.start_at), _fmt_dt(plan.end_at),
                           plan=plan, expected_rows=len(rows))
    res["stats"] = stats
    if res.get("ok") and phase == "commit":
        plan.status = "discount_pushed"
        db.commit()
    return res


def _signup_failure_signature(plan, result: dict) -> str:
    import hashlib
    import json
    payload = {
        "plan_id": getattr(plan, "id", None),
        "step": result.get("step"),
        "error": result.get("error"),
        "validation": result.get("validation"),
        "wrong_published_items": result.get("wrong_published_items"),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _notify_signup_failure(db: Session, plan, result: dict) -> dict:
    """同一失败内容只发一次飞书；原因变化后会重新通知。"""
    import json
    from app.services import notify_service, settings_service

    key = f"campaign_signup_failure_{getattr(plan, 'id', 'unknown')}"
    signature = _signup_failure_signature(plan, result)
    if settings_service.get(db, key, env_fallback=False) == signature:
        return {"deduped": True}
    validation = result.get("validation") or {}
    lines = [
        f"活动：{getattr(plan, 'name', '')}",
        f"千牛活动：{getattr(plan, 'qn_campaign_title', None) or getattr(plan, 'name', '')}",
        f"失败步骤：{result.get('step') or '批量导入/终态核对'}",
        f"原因：{result.get('error') or '未知错误'}",
    ]
    if isinstance(validation, dict):
        if any(k in validation for k in ("total_items", "ok", "failed")):
            lines.append(
                f"平台终态：总{validation.get('total_items')}品，"
                f"成功{validation.get('ok')}品，失败{validation.get('failed')}品"
            )
        reasons = validation.get("failed_reasons") or validation.get("failed_items")
        if reasons:
            lines.append("失败明细：" + json.dumps(
                reasons[:8] if isinstance(reasons, list) else reasons,
                ensure_ascii=False, default=str)[:1800])
    wrong = result.get("wrong_published_items")
    if wrong:
        lines.append("已发布错价/缺SKU：" + json.dumps(
            wrong[:8], ensure_ascii=False, default=str)[:1800])
    lines.append("系统已停止本次自动报名；不会盲目全量重推。")
    delivered = notify_service.broadcast_text(
        db, "\n".join(lines), title="活动自动报名失败", level="error")
    if any(v is True for v in delivered.values()):
        settings_service.set_value(
            db, key, signature, description="活动自动报名失败飞书通知去重签名")
        db.commit()
    return delivered


def _clear_signup_failure_dedupe(db: Session, plan) -> None:
    from app.services import settings_service
    settings_service.set_value(
        db, f"campaign_signup_failure_{getattr(plan, 'id', 'unknown')}", "")
    db.commit()


def push_signup(db: Session, plan) -> dict:
    """推大促报名 (channel promo_signup)。R12: 报名导入即报名成功 (stage 即生效, 无 commit 步)。
    回执自愈: 失败明细里的下架SKU/动销不达标商品自动登记。"""
    from app.services import campaign_recon_service, web_agent_service

    rows, stats = build_signup_rows(db, plan)
    if not rows:
        return {"ok": False, "error": "无可推送的报名行", "stats": stats}

    # 先只读导出当前活动：整品全部 SKU 已发布且活动价一致才视为正确；
    # 正确品不重复导入。若发现“已发布但错价”，批量导入无法安全修正，立即停并报告。
    title = plan.qn_campaign_title or plan.name
    exported = web_agent_service.campaign_export_items(db, title)
    if not exported.get("ok"):
        res = {"ok": False, "step": "current_state_export",
               "error": exported.get("error") or exported.get("message")
                        or "无法可靠取得当前活动生效集合",
               "stats": stats}
        res["notification"] = _notify_signup_failure(db, plan, res)
        return res
    live_rows = campaign_recon_service.parse_activity_items_export(exported["xlsx_bytes"])
    live_by_sku = {str(r["sku_id"]): r for r in live_rows}
    expected_by_item: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        expected_by_item[str(row["taobao_item_id"])].append(row)
    correct_items: set[str] = set()
    wrong_published: list[dict] = []
    for item_id, item_rows in expected_by_item.items():
        seen = [live_by_sku.get(str(r["taobao_sku_id"])) for r in item_rows]
        if all(x is not None for x in seen):
            mismatches = [
                {"sku_id": row["taobao_sku_id"], "expected": row["price"],
                 "actual": current.get("activity_price")}
                for row, current in zip(item_rows, seen)
                if current.get("activity_price") is None
                or abs(float(current["activity_price"]) - float(row["price"])) > 0.005
            ]
            if mismatches:
                wrong_published.append({"item_id": item_id, "mismatches": mismatches[:20]})
            else:
                correct_items.add(item_id)
        elif any(x is not None for x in seen):
            wrong_published.append({
                "item_id": item_id,
                "error": "已发布集合缺 SKU",
                "missing_skus": [
                    r["taobao_sku_id"] for r, current in zip(item_rows, seen)
                    if current is None
                ][:20],
            })
    stats["correct_items_excluded"] = sorted(correct_items)
    stats["wrong_published_items"] = wrong_published
    if wrong_published:
        res = {"ok": False, "step": "published_price_guard",
               "error": f"发现 {len(wrong_published)} 个已发布商品错价/缺SKU，拒绝用新增导入覆盖",
               "wrong_published_items": wrong_published, "stats": stats}
        res["notification"] = _notify_signup_failure(db, plan, res)
        return res

    pending = [r for r in rows if str(r["taobao_item_id"]) not in correct_items]
    pending_items = {str(r["taobao_item_id"]) for r in pending}
    stats["pending_items"] = sorted(pending_items)
    stats["pending_rows"] = len(pending)
    if not pending:
        plan.status = "signup_pushed"
        db.commit()
        _clear_signup_failure_dedupe(db, plan)
        return {"ok": True, "no_change": True, "stats": stats,
                "message": "当前活动中所有目标商品已发布且价格一致，无需重复导入"}

    res = _upload_and_wait(
        db, "promo_signup", "stage", _build_signup_xlsx(pending),
        _fmt_dt(plan.start_at), _fmt_dt(plan.end_at), plan=plan,
        expected_rows=len(pending), expected_items=len(pending_items))
    res["stats"] = stats
    _learn_from_validation(db, res.get("validation"))
    if res.get("ok"):
        plan.status = "signup_pushed"
        db.commit()
        _clear_signup_failure_dedupe(db, plan)
    else:
        res["notification"] = _notify_signup_failure(db, plan, res)
    return res


# ── 6. 核对器用的目标到手 (campaign_recon_service 消费) ────────────────────────

def target_prices(db: Session, plan) -> dict[str, dict]:
    """逐 skuId 的 {sku_code, target(目标到手, 不贴线), line, daily, signup_price,
    is_placeholder, kind}。目标 = 大促到手 / 中促到手(×1.03就地) / 无动销=中促+1;
    贴线判定交核对器 (它要区分"一分不差"与"贴线让X")。"""
    from app.services import no_sales_service
    tier = plan_tier(plan)
    lev = TIER_LEVERAGE[tier]
    nosales = no_sales_service.get_no_sales(db)
    out: dict[str, dict] = {}
    for s, p in _mapped_pairs(db):
        if not p.taobao_sku_id:
            continue
        placeholder = bool(getattr(s, "is_custom_placeholder", False))
        item_id = str(p.taobao_item_id).strip()
        daily = float(s.daily_price) if s.daily_price else None
        if placeholder:
            price, _remark = _placeholder_signup_price(s, p, lev)
            entry = {"sku_code": s.sku_code, "target": None, "line": None, "daily": daily,
                     "signup_price": price, "is_placeholder": True, "kind": "placeholder"}
        else:
            mid = mid_buyer_inplace(p)
            if item_id in nosales:
                if tier in ("big", "big618"):
                    no_sales_target = _d(getattr(p, "big_buyer_price", None))
                else:
                    no_sales_target = (
                        (mid + NOSALES_MARKUP_YUAN).quantize(_CENT) if mid else None)
                target = float(no_sales_target) if no_sales_target else None
                kind = "nosales"
            elif tier == "mid":
                target, kind = (float(mid) if mid else None), "campaign"
            else:
                big = _d(getattr(p, "big_buyer_price", None))
                target, kind = (float(big) if big and big > 0 else None), "campaign"
            line = _d(getattr(p, "coupon_floor_price", None))
            entry = {"sku_code": s.sku_code, "target": target,
                     "line": float(line) if line else None, "daily": daily,
                     "signup_price": daily, "is_placeholder": False, "kind": kind}
        for sid in _expand_sku_ids(p):
            out[sid] = entry
    return out
