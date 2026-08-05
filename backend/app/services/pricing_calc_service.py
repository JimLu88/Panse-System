"""定价计算工具 — 成本/毛利率重算。

公式:
  gross_margin_rate = (售价 − accounting_cost − tax − platform_fee_amount) / 售价
  big_promo_margin  = big_promo × (1 - platform_fee_rate) − accounting_cost − tax
  platform_fee_amount = 售价 × platform_fee_rate
"""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from typing import Optional

from sqlalchemy.orm import Session

from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuCosts, PricingSkuPromo

# 成本加成 gross-up 率 = 平台手续费 0.6% + 税 2% = 2.6% (对齐用户 Excel List 表 AH 列 "淘宝支付手续费")。
# 会计基准(=Excel 成本总计 O) = 物理成本 ÷ (1 − 此率); 各档价 = ROUNDUP(会计基准 ÷ 基数, −1)。
# 注: 这是"定价设计口径"; 逐单实际利润仍由 order_financials 按实付×费率另算(第16条 铁律不变)。
PRICING_GROSSUP_RATE = Decimal("0.026")

# 中促/大促 联动比 K = 中促到手 ÷ 大促到手。数学下限 = (1−10%)/(1−12%) = 0.90/0.88 ≈ 1.0227
# (超级立减10%场 vs 88VIP大促12%场官方力度之比): K ≥ 下限 才能"先报浅折(超级立减)、再报深折(88VIP)"
# 只往低报、不涨价、报得进 (用户 2026-07-15; 价格体系设置.md 第二铁律)。
# 2026-07-16: K 提为【可配旋钮】(settings: promo_mid_over_big_ratio), 大促到手是唯一不可动的锚。
# ★★2026-07-17 用户拍板(任务#22): 全店统一 K = 1.03 —— 中促到手 = 大促到手 × 1.03, 固化进定价链。
#   promo.mid_buyer_price 由 recompute_promo 按 big_buyer × K 派生, 【全链只此一个来源】。
#   (旧口径 = 从 sku.mid_promo ÷(1−佣金) 反推, 受 roundup10/cost-plus 基数影响逐 SKU 漂移 1.025~1.038, 已废。)
#   与 campaign_service.MID_OVER_BIG_RATIO(=1.03, 活动引擎就地计算版) 同口径, 交叉断言见 test_mid_ratio_unify_0717。
#   ⚠ K 只能在【新一轮重挂/重报】时抬 —— 活动进行中抬价 = 涨价, 撞平台"券后价≤近15天最低"必被拦。
_MID_OVER_BIG_RATIO_MIN = Decimal("0.90") / Decimal("0.88")   # 数学下限, K 不得低于此(低于=88VIP报不进)
_MID_OVER_BIG_RATIO = Decimal("1.03")                         # 默认值(2026-07-17 拍板: 1.03 全店统一)


def _mid_over_big_ratio(params: Optional[dict] = None) -> Decimal:
    """取中促/大促托底比 K (可配, 见上)。低于数学下限的配置值一律顶回下限 —— 防误配把 88VIP 报名整场废掉。"""
    if not params:
        return _MID_OVER_BIG_RATIO
    raw = params.get("mid_over_big_ratio")
    if raw in (None, ""):
        return _MID_OVER_BIG_RATIO
    try:
        k = Decimal(str(raw))
    except Exception:
        return _MID_OVER_BIG_RATIO
    return k if k >= _MID_OVER_BIG_RATIO_MIN else _MID_OVER_BIG_RATIO_MIN


def _d(v) -> Optional[Decimal]:
    return Decimal(str(v)) if v is not None else None


def _roundup10(x: Optional[Decimal]) -> Optional[Decimal]:
    """ROUNDUP(x, −1): 向上取整到最近的 10 (复刻 Excel List 表价格公式)。"""
    if x is None:
        return None
    return (Decimal(x) / Decimal("10")).to_integral_value(rounding=ROUND_CEILING) * Decimal("10")


def recompute(sku: PricingSku, params: Optional[dict] = None) -> None:
    """原地按【定价总表口径】重算整条 成本链 + (成本加成)价格链 + 利润链 (2026-07-01)。

    params: 可选活动参数(get_promo_params), 目前只用其 mid_over_big_ratio(=K 中促托底比)。
            不传 → K 用默认 1.03 (2026-07-17 拍板全店统一)。**K 只影响中促托底, 大促价永不被它改。**

    成本链(自下而上):
      工厂成本 = 木作 + 包装 + 外配件   (除非 factory_cost_override=True 手动覆盖 → 保留手改值)
      物理成本 = 工厂成本 + 物流 + 安装
    价格链(成本加成, cost-plus; **仅当该 SKU 有基数时**, 对齐用户 Excel List 表定价法):
      会计基准 = 物理成本 ÷ (1 − 2.6%)           (= Excel 成本总计 O)
      标价 = ROUNDUP(会计基准 ÷ base_list, −1) ; 日常 = 标价 × 0.75
      小促/中促/大促 = ROUNDUP(会计基准 ÷ base_small/mid/big, −1)
      无基数(base_* 全空)→ 跳过价格链, 大促价保持原输入值(不破坏未对齐 SKU / 既有测试)。
    利润链(依赖 大促价 K 与 物理成本 Q; 上面若联动改了大促, 这里随之全部重算):
      平台费 O = 大促价 × 0.6% ; 税 P = 大促价 × 2%
      会计成本 N = 物理成本 + 平台费 + 税
      大促利润 L = 大促价 − 会计成本 ; 毛利率 M = 大促利润 ÷ 大促价
    """
    # 定制占位符 (2026-07-07): 仅淘宝报名用, 不参与产品成本/价格链/利润计算 → 直接跳过, 保持 daily_price=现价 原值。
    if getattr(sku, "is_custom_placeholder", False):
        return
    cent = Decimal("0.01")
    zero = Decimal("0")
    # 1) 工厂成本: 未手动覆盖时 = 木作+包装+外配件 (改组件即联动); 覆盖时保留用户手改值
    if not getattr(sku, "factory_cost_override", False):
        wood, pack, ext = _d(sku.wood_cost), _d(sku.packaging_cost), _d(sku.external_parts_cost)
        if any(v is not None for v in (wood, pack, ext)):
            sku.factory_cost = ((wood or zero) + (pack or zero) + (ext or zero)).quantize(
                cent, rounding=ROUND_HALF_UP)
    # 2) 物理成本 = 工厂成本 + 物流 + 安装 (改工厂成本/物流/安装即联动)
    fac = _d(sku.factory_cost)
    if fac is not None:
        sku.physical_cost = (fac + (_d(sku.logistics_cost) or zero)
                             + (_d(sku.install_cost) or zero)).quantize(cent, rounding=ROUND_HALF_UP)
    # 2.5) 成本加成价格链 (cost-plus, 对齐 Excel): 仅当有基数时触发 → 成本一涨价格自动抬、保毛利。
    #      base_* 全空的 SKU 完全跳过此段, 大促价保持原样 (保护未对齐 SKU 与既有"大促当输入"测试)。
    phys = _d(sku.physical_cost)
    if phys is not None:
        acct_base = phys / (Decimal("1") - PRICING_GROSSUP_RATE)   # 会计基准 = Excel 成本总计 O
        b_list, b_small = _d(sku.base_list), _d(sku.base_small)
        b_mid, b_big = _d(sku.base_mid), _d(sku.base_big)
        if b_list:
            sku.list_price = _roundup10(acct_base / b_list)
            sku.daily_price = (sku.list_price * Decimal("0.75")).quantize(cent, rounding=ROUND_HALF_UP)
        if b_small:
            sku.small_promo = _roundup10(acct_base / b_small)
        if b_mid:
            sku.mid_promo = _roundup10(acct_base / b_mid)
        if b_big:
            sku.big_promo = _roundup10(acct_base / b_big)
    # 2.7) 中促自动联动大促【托底】(2026-07-15 引入保 88VIP 报得进; 2026-07-17 任务#22 K 统一 1.03):
    #      中促价 ≥ 大促价 × K(默认1.03) → 中促实收与"中促到手=大促到手×1.03"新口径对齐。
    #      手设更高的中促保留(不砍利润); 未设/低于托底的自动补到托底。对大促当输入/成本加成两种口径都生效。
    big = _d(sku.big_promo)
    if big is not None and big > 0:
        # 先 quantize 抹掉非整除 K 的 Decimal 残差(如 0.90/0.88 时 880×比值=900.0000…24 会被 ceiling 顶成 910), 再进位到10
        # ★大促价 big 在此【只读不写】—— 中促由大促派生, 绝无反向路径(用户铁律: 大促到手是唯一不可动的锚)。
        floor_mid = _roundup10((big * _mid_over_big_ratio(params)).quantize(cent, rounding=ROUND_HALF_UP))
        cur_mid = _d(sku.mid_promo)
        if cur_mid is None or cur_mid < floor_mid:
            sku.mid_promo = floor_mid
    # 3) 利润链
    big = _d(sku.big_promo)
    phys = _d(sku.physical_cost)
    if big is not None:
        sku.platform_fee_rate = (big * Decimal("0.006")).quantize(cent, rounding=ROUND_HALF_UP)
        sku.tax = (big * Decimal("0.02")).quantize(cent, rounding=ROUND_HALF_UP)
    pf = _d(sku.platform_fee_rate) or zero
    tax = _d(sku.tax) or zero
    if phys is not None:
        sku.accounting_cost = (phys + pf + tax).quantize(cent, rounding=ROUND_HALF_UP)
    cost = _d(sku.accounting_cost)
    if big is not None and big != 0 and cost is not None:
        margin = (big - cost).quantize(cent, rounding=ROUND_HALF_UP)
        sku.big_promo_margin = margin
        sku.gross_margin_rate = (margin / big).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def recompute_and_save(db: Session, sku_id: int) -> PricingSku:
    sku = db.get(PricingSku, sku_id)
    if not sku:
        raise ValueError(f"PricingSku {sku_id} not found")
    recompute(sku)
    db.commit()
    db.refresh(sku)
    return sku


# 活动价全局参数(按档)默认值 —— 复刻改造前口径: 平台立减(力度)12%; 88VIP佣金 中促1%/大促0%。
# 以此为默认时, 下面的到手价/店铺到账/会员价与改造前【完全一致】(核对吻合用)。
PROMO_PARAM_DEFAULTS = {
    "mid_platform_discount": "0.12", "mid_vip_commission": "0.01",
    "big_platform_discount": "0.12", "big_vip_commission": "0.00",
    # ★中促/大促联动比 K (2026-07-17 任务#22 拍板): 全店统一 1.03 —— 中促到手 = 大促到手 × 1.03。
    # 调大=中促抬高、大促锚不动。低于数学下限(0.90/0.88≈1.0227)会被 _mid_over_big_ratio 顶回。
    "mid_over_big_ratio": "1.03",
}
# 88VIP 消费券阶梯默认 (来自用户活动报名表): 到手价 ≥阈值 → 减额。降序匹配, 取满足的最高一档。
COUPON_TIERS_DEFAULT = [[1500, 150], [800, 80], [500, 50], [200, 20]]


def _coupon_deduction(amount, tiers):
    """按消费券阶梯求减额: 取「阈值 ≤ 到手价」中阈值最高那档的减额; 都不满足=0。"""
    from decimal import Decimal as D
    best = D("0")
    for thr, ded in sorted(tiers, key=lambda x: x[0], reverse=True):
        if amount >= thr:
            return D(str(ded))
    return best


def get_promo_params(db) -> dict:
    """读活动价全局参数(平台立减/88VIP佣金/消费券阶梯, 按中促/大促分档), 存 system_settings;
    没配过 → 用默认(平台立减/佣金=改造前口径; 消费券阶梯=活动表口径)。"""
    from decimal import Decimal as D
    import json
    from app.services import settings_service
    out: dict = {}
    for k, dflt in PROMO_PARAM_DEFAULTS.items():
        raw = settings_service.get(db, f"promo_{k}", env_fallback=False)
        try:
            out[k] = D(str(raw)) if raw not in (None, "") else D(dflt)
        except Exception:
            out[k] = D(dflt)
    # 消费券阶梯 (按档), 存 JSON: [[阈值, 减额], ...]
    for tier_key in ("mid_coupon_tiers", "big_coupon_tiers"):
        raw = settings_service.get(db, f"promo_{tier_key}", env_fallback=False)
        tiers = None
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list) and parsed:
                    tiers = [[D(str(a)), D(str(b))] for a, b in parsed]
            except Exception:
                tiers = None
        out[tier_key] = tiers or [[D(str(a)), D(str(b))] for a, b in COUPON_TIERS_DEFAULT]
    return out


def recompute_promo(promo: PricingSkuPromo, sku: PricingSku, params: Optional[dict] = None) -> None:
    """倒推活动价 (2026-07-02 用户改回 Excel「活动价」表方式)。

    输入 = 各档【店铺实收价】= 小促价/中促价/大促价 (存在 sku.small_promo/mid_promo/big_promo);
    输出 = 【单品立减系数】(空档期用单品立减把价做到此水平的那个数) + 买家到手/店铺到手/88VIP到手。
    口径 (小促/大促复刻用户 Excel Q/AB; 中促 2026-07-17 任务#22 改统一系数):
      日常价 = sku.daily_price
      小促: 无平台立减/佣金 → 买家到手 = 小促价(店铺实收); 单品立减系数 = 小促价 ÷ 日常
      大促: 买家到手 = 大促价 ÷ (1−大促佣金); 单品立减系数 = 买家到手 ÷ (日常 ×(1−大促立减));
            店铺到手 = 大促价(实收); 88VIP到手 = 买家到手 − 88VIP消费券(阶梯)
      中促(★任务#22, 2026-07-17 拍板): 买家到手 = 大促到手 × K(promo_mid_over_big_ratio, 默认1.03)
            —— mid_buyer_price【全链只此一个来源】, 不再从 sku.mid_promo 反推(旧口径漂移 1.025~1.038 已废);
            店铺到手 = 买家到手 × (1−中促佣金); 系数 = 买家到手 ÷ (日常 ×(1−中促立减));
            88VIP到手 = 买家到手 − 消费券(阶梯)。无大促价 → 中促各字段不动(唯一来源算不出, 绝不回退旧口径)。
    立减/佣金取全局参数(改参数→「活动参数」页一键全表重算); 记录回 promo 供前端展示。
    ⚠必须在 recompute(sku) 之后调 (要读到最新的 小促/大促价)。"""
    from decimal import Decimal as D, ROUND_HALF_UP
    if sku.daily_price is None or D(str(sku.daily_price)) == 0:
        return
    daily = D(str(sku.daily_price))
    p = params or {k: D(v) for k, v in PROMO_PARAM_DEFAULTS.items()}
    mid_disc = D(str(p.get("mid_platform_discount", PROMO_PARAM_DEFAULTS["mid_platform_discount"])))
    mid_comm = D(str(p.get("mid_vip_commission", PROMO_PARAM_DEFAULTS["mid_vip_commission"])))
    big_disc = D(str(p.get("big_platform_discount", PROMO_PARAM_DEFAULTS["big_platform_discount"])))
    big_comm = D(str(p.get("big_vip_commission", PROMO_PARAM_DEFAULTS["big_vip_commission"])))
    mid_tiers = p.get("mid_coupon_tiers") or [[D(str(a)), D(str(b))] for a, b in COUPON_TIERS_DEFAULT]
    big_tiers = p.get("big_coupon_tiers") or [[D(str(a)), D(str(b))] for a, b in COUPON_TIERS_DEFAULT]
    cent, rate_q = D("0.01"), D("0.000001")
    promo.taobao_activity_price = daily
    promo.xhs_list_price = daily
    # 小促: 店铺实收 = 小促价; 无立减/佣金 → 买家到手 = 店铺实收; 系数 = 小促价 ÷ 日常
    if sku.small_promo is not None:
        recv = D(str(sku.small_promo))
        promo.shop_internal_final = recv
        promo.shop_promo_rate = (recv / daily).quantize(rate_q, ROUND_HALF_UP)
    # 大促: 店铺实收 = 大促价 (先算 —— 任务#22 后中促由大促到手派生)
    big_buyer = None
    if sku.big_promo is not None and (D("1") - big_comm) != 0 and (D("1") - big_disc) != 0:
        recv = D(str(sku.big_promo))
        big_buyer = (recv / (D("1") - big_comm)).quantize(cent, ROUND_HALF_UP)
        promo.big_buyer_price = big_buyer
        promo.big_shop_receipt = recv
        promo.big_shop_rate = (big_buyer / (daily * (D("1") - big_disc))).quantize(rate_q, ROUND_HALF_UP)
        promo.big_vip_final = big_buyer - _coupon_deduction(big_buyer, big_tiers)
        promo.big_platform_discount = big_disc
        promo.big_vip_commission = big_comm
    # 中促 (★任务#22, 2026-07-17 拍板): 买家到手 = 大促到手 × K(默认1.03) —— 全链唯一来源。
    # 只在本次算出了大促到手时才写(绝不回退旧的 sku.mid_promo 反推口径, 也不读陈旧 big_buyer_price 字段)。
    if big_buyer is not None and big_buyer > 0 and (D("1") - mid_comm) != 0 and (D("1") - mid_disc) != 0:
        buyer = (big_buyer * _mid_over_big_ratio(params)).quantize(cent, ROUND_HALF_UP)
        promo.mid_buyer_price = buyer
        promo.mid_shop_receipt = (buyer * (D("1") - mid_comm)).quantize(cent, ROUND_HALF_UP)  # 到手×(1−佣金)
        promo.mid_shop_rate = (buyer / (daily * (D("1") - mid_disc))).quantize(rate_q, ROUND_HALF_UP)
        promo.mid_vip_final = buyer - _coupon_deduction(buyer, mid_tiers)
        promo.mid_platform_discount = mid_disc
        promo.mid_vip_commission = mid_comm
    # 小红书 (不变): 促销价 = 活动价 ×(1−折扣)
    if promo.xhs_activity_price:
        discount = promo.xhs_promo_discount if promo.xhs_promo_discount is not None else D("0.15")
        promo.xhs_promo_price = (D(str(promo.xhs_activity_price)) * (D("1") - discount)).quantize(cent, ROUND_HALF_UP)


def backfill_mid_buyer(db: Session, commit: bool = True, sample_limit: int = 20) -> dict:
    """★任务#22 一次性回填 (2026-07-17 拍板: 中促到手 = 大促到手 × 1.03 全店统一):
    全店 PricingSkuPromo 逐条重跑 recompute_promo(唯一来源) → mid_buyer_price = round(big_buyer×K, 2),
    连带 mid_shop_receipt / mid_shop_rate / mid_vip_final / 立减·佣金记录一起刷齐(旧漂移 1.025~1.038 归一)。

    ⚠只动 promo 派生链; 绝不调 recompute(sku) —— sku 四档价(标价/日常/中促/大促)与成本利润链一分不动。
    无大促价的 promo 跳过(唯一来源算不出, 原值保留); 无对应 sku / 日常价空的跳过。
    用法: from app.services import pricing_calc_service as pcs; stats = pcs.backfill_mid_buyer(db)
    返回 {scanned, changed, unchanged, skipped_no_sku, skipped_no_daily, skipped_no_big, ratio, samples}。"""
    from sqlalchemy import select as _select
    params = get_promo_params(db)
    ratio = _mid_over_big_ratio(params)
    sku_map = {s.sku_code: s for s in db.execute(_select(PricingSku)).scalars().all()}
    stats: dict = {"scanned": 0, "changed": 0, "unchanged": 0, "skipped_no_sku": 0,
                   "skipped_no_daily": 0, "skipped_no_big": 0,
                   "ratio": float(ratio), "samples": []}
    for promo in db.execute(_select(PricingSkuPromo)).scalars().all():
        stats["scanned"] += 1
        sku = sku_map.get(promo.sku_code)
        if sku is None:
            stats["skipped_no_sku"] += 1
            continue
        if sku.daily_price is None or Decimal(str(sku.daily_price)) == 0:
            stats["skipped_no_daily"] += 1          # recompute_promo 会直接 return, 提前计数说明原因
            continue
        if sku.big_promo is None:
            stats["skipped_no_big"] += 1            # 无大促价 → 中促唯一来源算不出, 原值保留
            continue
        before = _d(promo.mid_buyer_price)
        recompute_promo(promo, sku, params)
        after = _d(promo.mid_buyer_price)
        if before != after:
            stats["changed"] += 1
            if len(stats["samples"]) < sample_limit:
                stats["samples"].append({
                    "sku_code": promo.sku_code,
                    "before": float(before) if before is not None else None,
                    "after": float(after) if after is not None else None})
        else:
            stats["unchanged"] += 1
    if commit:
        db.commit()
    return stats


# ── 旧分析卡片模型（不得用于真实活动报名文件） ───────────────────────────────
# 2026-08-05 永久边界：真实 SKU 活动报名价只取 PricingSku.daily_price。
# 下列 signup_price_* 仅供历史页面/分析兼容，任何报名生成器、AI 或自动任务都不得调用它改价。
# 超级立减是「报名表直接手填一个数(报名价 A)」, 买家到手 = A × (1 − 场次力度)。三档场次力度:
#   中促(超值立减/88VIP场)10% · 大促(大促场)12% · 618/双11 15%。
#   —— 独立于旧 mid/big_platform_discount(那是店铺宝反推系数用的口径, 勿混)。
# 合规: 报名价 A 只报一个数, 要在中促场也报得进(不破平台线②最低普惠券后价), 必须
#   中促到手 ÷ 大促到手 ≥ (1−中促力度)/(1−大促力度) = 0.90/0.88 = 1.02273 (记 g_min)。
REPORT_LEVERAGE_DEFAULTS = {"mid": "0.10", "big": "0.12", "big618": "0.15"}

# ★报名安全垫 (2026-07-16 实战定): 报名价按【到手锚 − 此值】反算, 即主动让 2 元。
# 根因: 平台"校验期最低普惠券后价"是【上一轮真实到手】的历史快照, 而 ERP 锚会随成本/基数微调而漂移
# ——两者天然差几毛到几元, 且【无法预知】。实证:
#   黑胡桃木榻榻米-1.2米  锚4275.51 vs 线4274.71 (差0.80) → 券后4275 超线0.29 被拒
#   榉木柔光床-1.35米松木 锚3081.63 vs 线3079.83 (差1.80) → 券后3080 超线0.17 被拒
#   鎏金餐边柜-1.5米      锚13673.00 vs 线13672.87(差0.13)
# 这类"锚比线高不到2元"的漂移占失败的 34/135。**光靠学线治不了**: 线只能等平台回执才知道, 且回执里的
# "活动普惠券后价"是按【当次上传的价】算的, 代码一改就对不上号 → 反查匹配失败 → 没学到的照样撞(打地鼠)。
# 让 2 元一次性覆盖全部小漂移: 少赚 2 元 ≈ 锚的 0.05%(4275元的品) / 0.015%(13673元的品), 可忽略。
# 已学到确切线的 SKU(占位线350、真低线品)仍另走 coupon_floor 精确封顶, 两者取更严的。
SIGNUP_SAFETY_YUAN = Decimal("2")


def max_signup_price(target, lev) -> Optional[Decimal]:
    """★求【最大整数报名价 P】使 平台算出的活动券后价 ≤ target。
    2026-07-16 实证淘汰了简单的 floor(target/(1−lev)):

    平台把活动券后价【四舍五入到整元】再跟线比 —— 回执原文实证:
      报名价 15537 → 平台算"活动普惠券后价：13673.00元"(15537×0.88=13672.56, 入到 13673),
      而"最低普惠券后价：13672.87元" → 13673 > 13672.87 → 超线 0.13 元被拒。
      floor(13672.87/0.88)=15537 仍撞线 → 必须再退到 15536(13671.68→入 13672 ≤ 13672.87 ✓)。
    target 可以是【大促到手锚】(不超锚)或【券后线】(不超线); 两者都要满足时取 min(锚, 线) 传进来。
    返回 None 表示 target/lev 非法。"""
    t = _d(target)
    if t is None or t <= 0 or lev is None:
        return None
    k = Decimal("1") - Decimal(str(lev))
    if k <= 0:
        return None
    # ★平台用【精确算术】比线(显示才取2位小数) —— 2026-07-16 实证:
    #   报名价 56.93(占位) → 平台回执"活动普惠券后价：50.10元"(56.93×0.88=50.0984, 只是显示成50.10),
    #   与线 50.00 比时用的是 50.0984 > 50 → 判超线 0.10。
    #   ⇒ 曾误以为平台"入整元"(因见过 13673.00 这种整数, 实为价格本身带小数所致), 按那个模型放宽
    #     半元会正好卡死在边界上。故这里回到最严的口径: 券后价 = P×k 精确值, 必须 ≤ t。
    # 报名价填整数元(与千牛模板一致) ⇒ P_max = floor(t/k), 再夹逼抹掉 Decimal 除法残差。
    p = (t / k).quantize(Decimal("1"), ROUND_FLOOR)
    for _ in range(4):
        if p <= 0:
            return Decimal("0")
        if p * k <= t:                       # 精确比较, 不做任何取整
            break
        p -= 1
    for _ in range(4):                       # 向上探: 保证是最大的那个(不白少报)
        if (p + 1) * k > t:
            break
        p += 1
    return p if p > 0 else Decimal("0")


def _report_leverage(params: Optional[dict]):
    p = params or {}
    return (
        Decimal(str(p.get("report_mid_leverage", REPORT_LEVERAGE_DEFAULTS["mid"]))),
        Decimal(str(p.get("report_big_leverage", REPORT_LEVERAGE_DEFAULTS["big"]))),
        Decimal(str(p.get("report_618_leverage", REPORT_LEVERAGE_DEFAULTS["big618"]))),
    )


def report_prices(promo: PricingSkuPromo, params: Optional[dict] = None) -> dict:
    """旧分析卡片：由中/大促到手价派生理论值（纯读，不得用于真实活动报名）:
      报名价 A   = 大促到手 ÷ (1−大促力度12%)  —— 锚在大促, 大促价一分不动;
      A中        = 中促到手 ÷ (1−中促力度10%)  (合规时应 = A);
      618报名价  = 大促到手 ÷ (1−618力度15%);
      空档价红线 = 中促到手 (空档期任何单件即享工具做的价不得低于它, 否则塌平台线②);
      g          = 中促到手 ÷ 大促到手; 合规 ⟺ g ≥ g_min(=0.90/0.88)。
    报名价取整到元(用户实际填淘宝的数); 佣金已内含在到手值里, 与佣金参数无关。

    ★2026-07-16 报名价重构新增 (用户四诉求: 大促到手是唯一锚 / 只维护一个数 / 单品立减降为自动层 /
      平台低价校验永远自洽)。

    旧「叠加法」病灶: 报名活动价填【日常价】(高), 靠一大刀单品立减压到到手, 而平台算的【名义券后】
      = 活动价×(1−比例) **不含单品立减** → 名义比真实到手虚高整整一刀立减(全店中位 ¥1387 = 日常价23%)
      → 名义券后必顶穿"近15天最低券后"历史线 → 报名失败(2026-07-16 88VIP 60品报 42 失败)。

    ★★为什么【垫片不能常驻】(2026-07-16 实证推翻了"常驻垫片"初稿, 记死):
      单品立减只能【减钱】⇒ 真实到手 = 名义券后 − 垫片 ≤ 名义券后;
      平台历史线 = **真实到手**(实证: AA柱2.1米 线 9459.18 == 大促锚 9459.18 一分不差);
      ⇒ 下一轮 名义 必须 ≤ 上轮真实 = 上轮名义 − 垫片。**垫片>0 ⇒ 名义须逐轮下降 ⇒ 到手锚保不住。**
      实测: 带垫片方案下一轮大促 409/409 全部报不进; 垫片越厚死得越快。
      ⇒ 稳态【垫片必须 = 0】: 报名价 = floor(到手 ÷ (1−该场比例)), 名义 = 真实 = 到手 →
        线 = 名义 → 下轮名义 = 同值 ≤ 线 → **永远自洽、可无限重复**(实测 0/409 撞线, 取整让步中位 ¥0.41)。
      单品立减保留但**只在两处用**: ① 空档期(无活动时压价, 不参与报名校验); ② 一次性救场(见下)。

    输出 (signup_price_* = 各场该填的报名价, 垫片恒 0):
      signup_price_big = floor(大促到手 ÷ (1−12%))  ← 88VIP/大促场; 到手 = 大促锚 ✔
      signup_price_mid = floor(中促到手 ÷ (1−10%))  ← 超级立减场; 到手 = 中促
      signup_price_618 = floor(大促到手 ÷ (1−15%))  ← 618/双11
      K=0.90/0.88(数学下限) 时 signup_price_big == signup_price_mid(**一个数管两场**); K 调大则两场各用各的
      (手调旋钮仍只有【大促锚】一个, 两个报名价都是系统派生)。★任务#22 后默认 K=1.03 > 下限 → 两场各用各的。
    ★救场不是"加垫片"(垫片只会把到手压得更低), 而是**把该 SKU 报名价压到线内**:
      报名价' = min(本场 signup_price, floor(券后线 ÷ (1−比例)))  —— 见 data_export_service._coupon_floor_cap。"""
    mid_lev, big_lev, lev618 = _report_leverage(params)
    yuan, micro, cent = Decimal("1"), Decimal("0.000001"), Decimal("0.01")
    mid_buyer = _d(getattr(promo, "mid_buyer_price", None))
    big_buyer = _d(getattr(promo, "big_buyer_price", None))
    out = {"report_price": None, "report_price_mid": None, "report_price_618": None,
           "gap_floor": None, "compliance_g": None, "report_compliant": None,
           "signup_price_big": None, "signup_price_mid": None, "signup_price_618": None,
           "nominal_big": None, "anchor_slip_big": None}
    if big_buyer and big_buyer > 0 and (Decimal("1") - big_lev) != 0:
        out["report_price"] = (big_buyer / (Decimal("1") - big_lev)).quantize(yuan, ROUND_HALF_UP)
        # ★max_signup_price(锚 − 安全垫): 保证【平台口径】券后价(入整元) ≤ 锚−1元
        #   → 既不超锚, 又能吃掉"平台线比ERP锚低几毛"的不可预知漂移(见 SIGNUP_SAFETY_YUAN)。
        sp_big = max_signup_price(big_buyer - SIGNUP_SAFETY_YUAN, big_lev)
        out["signup_price_big"] = sp_big
        out["nominal_big"] = (sp_big * (Decimal("1") - big_lev)).quantize(cent, ROUND_HALF_UP)
        # 相对锚的取整让步(元, ≥0 且 <1): 报名价只能整数元 → 到手比锚低这么点, 中位 ¥0.41
        out["anchor_slip_big"] = (big_buyer - out["nominal_big"]).quantize(cent, ROUND_HALF_UP)
        if (Decimal("1") - lev618) != 0:
            out["report_price_618"] = (big_buyer / (Decimal("1") - lev618)).quantize(yuan, ROUND_HALF_UP)
            out["signup_price_618"] = max_signup_price(big_buyer - SIGNUP_SAFETY_YUAN, lev618)
    if mid_buyer and mid_buyer > 0 and (Decimal("1") - mid_lev) != 0:
        out["report_price_mid"] = (mid_buyer / (Decimal("1") - mid_lev)).quantize(yuan, ROUND_HALF_UP)
        out["gap_floor"] = mid_buyer.quantize(cent, ROUND_HALF_UP)
        out["signup_price_mid"] = max_signup_price(mid_buyer - SIGNUP_SAFETY_YUAN, mid_lev)
    if mid_buyer and big_buyer and big_buyer > 0:
        g = (mid_buyer / big_buyer).quantize(micro, ROUND_HALF_UP)
        g_min = (Decimal("1") - mid_lev) / (Decimal("1") - big_lev)
        out["compliance_g"] = g
        out["report_compliant"] = bool(g >= g_min - Decimal("0.0001"))
    return out


def single_item_discounts(promo: PricingSkuPromo, daily_price, params: Optional[dict] = None) -> dict:
    """由 中/大促【买家到手】+ 各场官方立减力度, 算淘宝『单品立减(单品补贴)』该填的 折扣 + 立减金额。

    ★加法口径 (2026-07-06 用户附图核准): 淘宝官方大促 = 官方立减 + 单品补贴 两个折扣【从活动价各自减】,
    不是乘法(旧 shop_rate 口径按 日常×(1−力度)×系数 是错的, 会差几百块)。活动价 = 日常价 = 标价×0.75:
      到手 = 日常价 − 日常价×官方力度 − 单品立减金额
      ⇒ 单品立减折 = 到手 ÷ 日常价 + 官方力度 ;  单品立减金额 = 日常价×(1−官方力度) − 到手
    三档场次(官方力度, 目标到手): 中促(日常 10% → 中促买家价) / 大促(88VIP 12% → 大促买家价) /
    超大促(618·双11 15% → 大促买家价, 同价换 SKU)。
    折 ≥ 1(官方立减已 ≥ 目标, 单品立减无从做起)→ 该档 None(不给假数)。纯派生, 不落库。

    返回 {mid_discount/mid_deduct, big_discount/big_deduct, big618_discount/big618_deduct}
      *_discount = 折扣(小数, 0.7920 = 7.92 折 = 买家付日常价的 79.2%); *_deduct = 立减金额(元)。"""
    mid_lev, big_lev, lev618 = _report_leverage(params)
    daily = _d(daily_price)
    out = {"mid_discount": None, "mid_deduct": None,
           "big_discount": None, "big_deduct": None,
           "big618_discount": None, "big618_deduct": None}
    if not daily or daily <= 0:
        return out
    cent, q4 = Decimal("0.01"), Decimal("0.0001")

    def _one(buyer, lev):
        if buyer is None or buyer <= 0:
            return None, None
        disc = buyer / daily + lev                       # 单品立减折
        if disc >= Decimal("1"):                          # 官方立减已 ≥ 目标 → 单品立减无从做起
            return None, None
        deduct = daily * (Decimal("1") - lev) - buyer     # 立减金额 = 日常×(1−力度) − 到手
        return disc.quantize(q4, ROUND_HALF_UP), deduct.quantize(cent, ROUND_HALF_UP)

    mid_buyer = _d(getattr(promo, "mid_buyer_price", None))
    big_buyer = _d(getattr(promo, "big_buyer_price", None))
    out["mid_discount"], out["mid_deduct"] = _one(mid_buyer, mid_lev)
    out["big_discount"], out["big_deduct"] = _one(big_buyer, big_lev)
    out["big618_discount"], out["big618_deduct"] = _one(big_buyer, lev618)
    return out


def fix_mid_to_compliant(sku: PricingSku, params: Optional[dict] = None) -> Optional[dict]:
    """若该 SKU 不合规(g<g_min), 抬【中促实收】令 中促到手 = 大促到手 × g_min(=0.90/0.88), 大促价一分不动。
    只改 sku.mid_promo, 并清 sku.base_mid(=None)让 recompute 不用 cost-plus 覆盖此手改中促。
    返回 {前后对比} 供 dry-run 展示; 已合规 / 缺大促或中促值 → None(不动)。
    ⚠caller 改后需 recompute(sku) + recompute_promo(promo, sku, params) 刷新到手/系数。"""
    mid_lev, big_lev, _ = _report_leverage(params)
    p = params or {}
    mid_comm = Decimal(str(p.get("mid_vip_commission", PROMO_PARAM_DEFAULTS["mid_vip_commission"])))
    big_comm = Decimal(str(p.get("big_vip_commission", PROMO_PARAM_DEFAULTS["big_vip_commission"])))
    big, mid = _d(sku.big_promo), _d(sku.mid_promo)
    if not big or not mid or big <= 0 or mid <= 0:
        return None
    if (Decimal("1") - big_comm) == 0 or (Decimal("1") - mid_comm) == 0:
        return None
    big_buyer = big / (Decimal("1") - big_comm)            # 大促到手
    cur_mid_buyer = mid / (Decimal("1") - mid_comm)        # 当前中促到手
    g_min = (Decimal("1") - mid_lev) / (Decimal("1") - big_lev)   # 0.90/0.88 = 1.02273
    g = cur_mid_buyer / big_buyer
    if g >= g_min - Decimal("0.0001"):
        return None                                        # 已合规, 不动
    # 目标中促实收(到手=大促到手×g_min, 再×(1−佣金))。合规要求 中促≥该 floor, 故向【上】取整到分。
    # 先按 0.0001 HALF_UP 抹掉 Decimal 非整除残差(g_min=0.90/0.88 不终止, 880×g_min=900.0000…24),
    # 再 CEILING 到分: 干净值(900.0000)不被多顶一分, 真有零头(¥20 样块 20.4545)才进位到 20.46, 保证 g≥g_min。
    target_mid_recv = (big_buyer * g_min * (Decimal("1") - mid_comm)).quantize(
        Decimal("0.0001"), ROUND_HALF_UP).quantize(Decimal("0.01"), ROUND_CEILING)
    before = mid
    sku.mid_promo = target_mid_recv
    sku.base_mid = None                                    # 清基数: recompute 不再 cost-plus 覆盖手改中促
    q4 = Decimal("0.0001")
    return {
        "sku_code": sku.sku_code, "product_code": sku.product_code,
        "product_name": getattr(sku, "product_name", None), "sku": getattr(sku, "sku", None),
        "big_promo": float(big), "mid_before": float(before), "mid_after": float(target_mid_recv),
        "g_before": float(g.quantize(q4, ROUND_HALF_UP)), "g_min": float(g_min.quantize(q4, ROUND_HALF_UP)),
    }


def recompute_costs(costs: PricingSkuCosts, sku: PricingSku) -> None:
    """根据 22 项配件成本重算 sku.external_parts_cost (sum of all non-None cost fields)."""
    from decimal import Decimal as D
    COST_FIELDS = [
        "rock_slab","drawer_rail","led_strip","glass","electric_rail","packing_sheet",
        "iron_pin","connector","aluminum_rail","plastic_rail","mini_handle","nail_free_glue",
        "engraving","acrylic_strip","embedded_sleeve","cable_mgmt","back_panel","stainless_trim",
        "leg","soft_pack","bed_board","other_cost",
    ]
    total = sum((getattr(costs, f) or D("0")) for f in COST_FIELDS)
    sku.external_parts_cost = total if total > 0 else None
