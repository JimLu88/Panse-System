"""定价计算工具 — 成本/毛利率重算。

公式:
  gross_margin_rate = (售价 − accounting_cost − tax − platform_fee_amount) / 售价
  big_promo_margin  = big_promo × (1 - platform_fee_rate) − accounting_cost − tax
  platform_fee_amount = 售价 × platform_fee_rate
"""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
from typing import Optional

from sqlalchemy.orm import Session

from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuCosts, PricingSkuPromo

# 成本加成 gross-up 率 = 平台手续费 0.6% + 税 2% = 2.6% (对齐用户 Excel List 表 AH 列 "淘宝支付手续费")。
# 会计基准(=Excel 成本总计 O) = 物理成本 ÷ (1 − 此率); 各档价 = ROUNDUP(会计基准 ÷ 基数, −1)。
# 注: 这是"定价设计口径"; 逐单实际利润仍由 order_financials 按实付×费率另算(第16条 铁律不变)。
PRICING_GROSSUP_RATE = Decimal("0.026")


def _d(v) -> Optional[Decimal]:
    return Decimal(str(v)) if v is not None else None


def _roundup10(x: Optional[Decimal]) -> Optional[Decimal]:
    """ROUNDUP(x, −1): 向上取整到最近的 10 (复刻 Excel List 表价格公式)。"""
    if x is None:
        return None
    return (Decimal(x) / Decimal("10")).to_integral_value(rounding=ROUND_CEILING) * Decimal("10")


def recompute(sku: PricingSku) -> None:
    """原地按【定价总表口径】重算整条 成本链 + (成本加成)价格链 + 利润链 (2026-07-01)。

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
    口径复刻用户 Excel Q/U/AB:
      日常价 = sku.daily_price
      小促: 无平台立减/佣金 → 买家到手 = 小促价(店铺实收); 单品立减系数 = 小促价 ÷ 日常
      中促: 买家到手 = 中促价 ÷ (1−中促佣金); 单品立减系数 = 买家到手 ÷ (日常 ×(1−中促立减));
            店铺到手 = 中促价(实收); 88VIP到手 = 买家到手 − 88VIP消费券(阶梯)
      大促: 同中促, 换大促价/大促佣金/大促立减
    立减/佣金取全局参数(改参数→「活动参数」页一键全表重算); 记录回 promo 供前端展示。
    ⚠必须在 recompute(sku) 之后调 (要读到最新的 小促/中促/大促价)。"""
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
    # 中促: 店铺实收 = 中促价
    if sku.mid_promo is not None and (D("1") - mid_comm) != 0 and (D("1") - mid_disc) != 0:
        recv = D(str(sku.mid_promo))
        buyer = (recv / (D("1") - mid_comm)).quantize(cent, ROUND_HALF_UP)      # 买家到手(不含消费券)
        promo.mid_buyer_price = buyer
        promo.mid_shop_receipt = recv                                          # 店铺到手 = 实收 = 中促价
        promo.mid_shop_rate = (buyer / (daily * (D("1") - mid_disc))).quantize(rate_q, ROUND_HALF_UP)
        promo.mid_vip_final = buyer - _coupon_deduction(buyer, mid_tiers)
        promo.mid_platform_discount = mid_disc
        promo.mid_vip_commission = mid_comm
    # 大促: 店铺实收 = 大促价
    if sku.big_promo is not None and (D("1") - big_comm) != 0 and (D("1") - big_disc) != 0:
        recv = D(str(sku.big_promo))
        buyer = (recv / (D("1") - big_comm)).quantize(cent, ROUND_HALF_UP)
        promo.big_buyer_price = buyer
        promo.big_shop_receipt = recv
        promo.big_shop_rate = (buyer / (daily * (D("1") - big_disc))).quantize(rate_q, ROUND_HALF_UP)
        promo.big_vip_final = buyer - _coupon_deduction(buyer, big_tiers)
        promo.big_platform_discount = big_disc
        promo.big_vip_commission = big_comm
    # 小红书 (不变): 促销价 = 活动价 ×(1−折扣)
    if promo.xhs_activity_price:
        discount = promo.xhs_promo_discount if promo.xhs_promo_discount is not None else D("0.15")
        promo.xhs_promo_price = (D(str(promo.xhs_activity_price)) * (D("1") - discount)).quantize(cent, ROUND_HALF_UP)


# ── 报名价模型 (2026-07-03: 店铺宝失效 → 超级立减「报名价」; 大促价为锚不动, 只动中促) ──────────
# 超级立减是「报名表直接手填一个数(报名价 A)」, 买家到手 = A × (1 − 场次力度)。三档场次力度:
#   中促(超值立减/88VIP场)10% · 大促(大促场)12% · 618/双11 15%。
#   —— 独立于旧 mid/big_platform_discount(那是店铺宝反推系数用的口径, 勿混)。
# 合规: 报名价 A 只报一个数, 要在中促场也报得进(不破平台线②最低普惠券后价), 必须
#   中促到手 ÷ 大促到手 ≥ (1−中促力度)/(1−大促力度) = 0.90/0.88 = 1.02273 (记 g_min)。
REPORT_LEVERAGE_DEFAULTS = {"mid": "0.10", "big": "0.12", "big618": "0.15"}


def _report_leverage(params: Optional[dict]):
    p = params or {}
    return (
        Decimal(str(p.get("report_mid_leverage", REPORT_LEVERAGE_DEFAULTS["mid"]))),
        Decimal(str(p.get("report_big_leverage", REPORT_LEVERAGE_DEFAULTS["big"]))),
        Decimal(str(p.get("report_618_leverage", REPORT_LEVERAGE_DEFAULTS["big618"]))),
    )


def report_prices(promo: PricingSkuPromo, params: Optional[dict] = None) -> dict:
    """由 promo 的中/大促【买家到手】派生报名价模型输出 (纯读, 不改库):
      报名价 A   = 大促到手 ÷ (1−大促力度12%)  —— 锚在大促, 大促价一分不动;
      A中        = 中促到手 ÷ (1−中促力度10%)  (合规时应 = A);
      618报名价  = 大促到手 ÷ (1−618力度15%);
      空档价红线 = 中促到手 (空档期任何单件即享工具做的价不得低于它, 否则塌平台线②);
      g          = 中促到手 ÷ 大促到手; 合规 ⟺ g ≥ g_min(=0.90/0.88)。
    报名价取整到元(用户实际填淘宝的数); 佣金已内含在到手值里, 与佣金参数无关。"""
    mid_lev, big_lev, lev618 = _report_leverage(params)
    yuan, micro = Decimal("1"), Decimal("0.000001")
    mid_buyer = _d(getattr(promo, "mid_buyer_price", None))
    big_buyer = _d(getattr(promo, "big_buyer_price", None))
    out = {"report_price": None, "report_price_mid": None, "report_price_618": None,
           "gap_floor": None, "compliance_g": None, "report_compliant": None}
    if big_buyer and big_buyer > 0 and (Decimal("1") - big_lev) != 0:
        out["report_price"] = (big_buyer / (Decimal("1") - big_lev)).quantize(yuan, ROUND_HALF_UP)
        if (Decimal("1") - lev618) != 0:
            out["report_price_618"] = (big_buyer / (Decimal("1") - lev618)).quantize(yuan, ROUND_HALF_UP)
    if mid_buyer and mid_buyer > 0 and (Decimal("1") - mid_lev) != 0:
        out["report_price_mid"] = (mid_buyer / (Decimal("1") - mid_lev)).quantize(yuan, ROUND_HALF_UP)
        out["gap_floor"] = mid_buyer.quantize(Decimal("0.01"), ROUND_HALF_UP)
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
