"""报名价体系重构 (2026-07-16 用户四诉求): 报名价法 + 垫片=0 + K可配 + 券后线封顶。

四诉求 → 四组断言:
① 大促到手是唯一不可动的锚      → test_k_never_touches_big_promo (改K前后 big_promo 逐SKU相等)
② 只维护一个数(K=下限时两场同价) → test_one_number_when_k_at_floor
③ 平台低价校验永远自洽(名义=真实, 可无限重复) → test_no_ratchet_signup_price_repeatable
④ 中促可抬高(K旋钮), 大促不动    → test_k_raises_mid_only

外加机制护栏:
- test_coupon_floor_cap_math        : 券后线反解封顶(救场=压报名价, 不是加垫片)
- test_placeholder_eats_coupon_floor: 占位吃券后线(治"3个占位拖垮8个真SKU整品被拒")
"""
from decimal import Decimal

import pytest

from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo
from app.services import pricing_calc_service as pcs

BIG_LEV = Decimal("0.12")   # 88VIP 官方立减
MID_LEV = Decimal("0.10")   # 超级立减 官方立减
G_MIN = Decimal("0.90") / Decimal("0.88")


def _promo(big_buyer=None, mid_buyer=None, coupon_floor=None):
    return PricingSkuPromo(
        sku_code="PPSTEST01", taobao_item_id="1001", taobao_sku_id="2001",
        big_buyer_price=Decimal(str(big_buyer)) if big_buyer is not None else None,
        mid_buyer_price=Decimal(str(mid_buyer)) if mid_buyer is not None else None,
        coupon_floor_price=Decimal(str(coupon_floor)) if coupon_floor is not None else None,
    )


# ── ① 大促锚不可动 ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("k", ["1.0227272727272727", "1.04", "1.10"])
def test_k_never_touches_big_promo(k):
    """★用户第一铁律: 改 K(中促托底) 前后, 大促价必须逐 SKU 一分不差。
    中促由大促派生, 代码里绝无反向路径 —— 这条断言把它钉死。"""
    def _sku():
        return PricingSku(product_code="P1", sku_code="PPSTEST01",
                          big_promo=Decimal("1000"), mid_promo=Decimal("1030"),
                          daily_price=Decimal("2000"))
    base, tuned = _sku(), _sku()
    pcs.recompute(base)                                        # 默认 K
    pcs.recompute(tuned, {"mid_over_big_ratio": k})            # 调大 K
    assert tuned.big_promo == base.big_promo == Decimal("1000"), "改K动了大促价=违反第一铁律"


def test_k_raises_mid_only():
    """④ 中促可抬高: K 从下限→1.04, 中促托底抬高, 大促纹丝不动。"""
    sku = PricingSku(product_code="P1", sku_code="PPSTEST01",
                     big_promo=Decimal("1000"), mid_promo=None, daily_price=Decimal("2000"))
    pcs.recompute(sku, {"mid_over_big_ratio": "1.04"})
    assert sku.big_promo == Decimal("1000")
    assert sku.mid_promo >= Decimal("1000") * Decimal("1.04"), "K=1.04 没把中促托底抬上去"


def test_k_below_math_floor_is_clamped():
    """误配护栏: K 低于数学下限(0.90/0.88) 会让 88VIP 报不进 → 一律顶回下限。"""
    assert pcs._mid_over_big_ratio({"mid_over_big_ratio": "1.00"}) == pcs._MID_OVER_BIG_RATIO_MIN
    assert pcs._mid_over_big_ratio({"mid_over_big_ratio": "abc"}) == pcs._MID_OVER_BIG_RATIO
    assert pcs._mid_over_big_ratio(None) == pcs._MID_OVER_BIG_RATIO


# ── ②③ 报名价法: 名义=真实, 一个数, 无棘轮 ────────────────────────────────────
def test_one_number_when_k_at_floor():
    """② K=数学下限时 中促到手 = 大促到手×g_min → 两场报名价【同一个数】(只维护一个数)。
    安全垫按各场自己的到手基数扣, 故两场可差 1 元取整残差 —— 仍是"同一个数"的工程含义。"""
    big = Decimal("1000")
    mid = (big * G_MIN).quantize(Decimal("0.01"))
    out = pcs.report_prices(_promo(big_buyer=big, mid_buyer=mid))
    diff = abs(out["signup_price_big"] - out["signup_price_mid"])
    assert diff <= 1, (
        f"K=下限时两场报名价应相同(±1元取整), 实际 big={out['signup_price_big']} "
        f"mid={out['signup_price_mid']} 差={diff}")


@pytest.mark.parametrize("big_buyer", ["1000", "928.57", "9459.18", "2683.67", "20.41"])
def test_no_ratchet_signup_price_repeatable(big_buyer):
    """★③ 核心: 名义券后 = 报名价×(1−12%) 必须 ≤ 大促到手(绝不超锚/超线)。

    这保证 线(=真实到手=名义) → 下一轮名义 = 同值 ≤ 线 → **可无限重复报名**。
    反例(已被推翻的"常驻垫片"方案): 垫片>0 → 名义 = 真实+垫片 > 线 → 下轮 409/409 报不进。
    """
    big = Decimal(big_buyer)
    out = pcs.report_prices(_promo(big_buyer=big, mid_buyer=big * G_MIN))
    sp = out["signup_price_big"]
    nominal = (sp * (Decimal("1") - BIG_LEV)).quantize(Decimal("0.01"))
    assert nominal <= big, f"名义券后 {nominal} 超过大促锚 {big} → 下一轮必被拦"
    # 让步 = 安全垫(1元, 吃平台线漂移) + 取整残差(<1元) ⇒ 恒 <2元
    slip = big - nominal
    assert Decimal("0") <= slip < pcs.SIGNUP_SAFETY_YUAN + 1, f"让步 {slip} 越界"
    assert out["anchor_slip_big"] == slip


@pytest.mark.parametrize("anchor,line", [("4275.51", "4274.71"), ("3081.63", "3079.83"),
                                         ("3775.51", "3775.11"), ("20.41", "19.80")])
def test_safety_margin_absorbs_line_drift(anchor, line):
    """★安全垫的意义(实证驱动): 平台线比 ERP 锚低几毛(不可预知, 学不全) —— 让1元即可吃掉。
    实证: 黑胡桃木榻榻米-1.2米 锚4275.51/线4274.71(差0.80) 曾超线0.29被拒; 让1元后过线。"""
    a, L = Decimal(anchor), Decimal(line)
    out = pcs.report_prices(_promo(big_buyer=a, mid_buyer=a * G_MIN))
    sp = out["signup_price_big"]
    assert _platform_coupon(sp) <= L, (
        f"锚{a} 线{L}: 让了安全垫仍超线(平台券后={_platform_coupon(sp)})")


def test_signup_price_beats_old_daily_method():
    """回归对照(真实数据: 榉木岩板餐桌1.4米 大促到手2683.67 / 日常价3787.50):
    旧叠加法报日常价 → 名义券后虚高【整整一刀单品立减】→ 撞线; 新报名价法 名义=到手 → 不撞。"""
    big, daily = Decimal("2683.67"), Decimal("3787.50")
    out = pcs.report_prices(_promo(big_buyer=big, mid_buyer=big * G_MIN))
    nominal_new = out["signup_price_big"] * (Decimal("1") - BIG_LEV)
    nominal_old = daily * (Decimal("1") - BIG_LEV)               # 旧口径: 活动价=日常价
    assert nominal_new <= big < nominal_old, "新口径应贴锚、旧口径应虚高"
    # ★虚高量 == 该SKU旧 big 档单品立减(暗刀) = 日常价×0.88 − 大促到手, 误差仅报名价取整残差(<1元)
    old_pad = nominal_old - big                                  # 这一刀 = 649.33
    assert nominal_old - nominal_new == pytest.approx(old_pad + out["anchor_slip_big"], abs=Decimal("0.01"))
    assert old_pad / daily > Decimal("0.15"), "暗刀应占日常价一大截(全店中位23%)"


# ── 平台算法建模 (2026-07-16 回执实证: 精确算术, 显示才2位小数) ──────────────────
def _platform_coupon(P, lev=BIG_LEV):
    """复刻平台算法: 活动券后价 = 报名价×(1−比例) 的【精确值】(回执里显示成2位小数而已)。"""
    return Decimal(str(P)) * (Decimal("1") - lev)


def test_platform_uses_exact_arithmetic_real_case():
    """★真实回执复刻(占位"其他定制"): 报名价 56.93 → 平台报"活动普惠券后价：50.10元",
    实为 56.93×0.88=50.0984 的显示值; 与线 50.00 比时用精确值 → 超 0.0984 被拒。
    ⇒ 平台【不】入整元; 若按"入整元"建模会放宽半元、正好卡死在这种边界。"""
    exact = Decimal("56.93") * Decimal("0.88")
    assert exact > Decimal("50"), "56.93 的券后精确值就是超 50 的线"
    assert exact.quantize(Decimal("0.01")) == Decimal("50.10"), "回执显示的 50.10 = 精确值取2位"
    got = pcs.max_signup_price(Decimal("50"), BIG_LEV)
    assert _platform_coupon(got) <= Decimal("50"), f"P={got} 券后={_platform_coupon(got)} 仍超50"


@pytest.mark.parametrize("target", ["13672.87", "4274.71", "350", "325.41", "928.27",
                                    "9459.18", "3152.66", "50", "250", "2832.34", "2769.91"])
def test_max_signup_price_never_over_platform_line(target):
    """不变量: max_signup_price 出的报名价, 按【平台口径(精确)】算的券后价必须 ≤ target(一分不超)。"""
    t = Decimal(target)
    p = pcs.max_signup_price(t, BIG_LEV)
    assert _platform_coupon(p) <= t, f"target={t} P={p} 平台券后={_platform_coupon(p)} 超了"
    # 且必须是【最大】的那个: P+1 一定超
    assert _platform_coupon(p + 1) > t, f"target={t} P={p} 不是最大(P+1 也没超)"


def test_coupon_floor_cap_uses_platform_rounding():
    """券后线封顶走 max_signup_price(平台四舍五入口径), 不是裸 floor(L/0.88)。"""
    from app.services.data_export_service import _coupon_floor_cap
    assert _coupon_floor_cap(_promo(coupon_floor=None), 0.12) is None      # 无线数据=不封顶
    for L in ("350", "325.41", "928.27", "9459.18", "13672.87"):
        cap = _coupon_floor_cap(_promo(coupon_floor=L), 0.12)
        assert _platform_coupon(cap) <= Decimal(L), f"L={L} 封顶后按平台口径仍超线"


def test_stale_legacy_floor_cannot_lower_real_signup(db_session):
    """旧缓存即使是730，也不得改写真实SKU报名价；新资格证据另走预检。"""
    from app.services.data_export_service import collect_signup_rows
    db = db_session
    db.add(PricingSku(product_code="P9", sku_code="PPS2521010041011",
                      daily_price=Decimal("5475"), is_custom_placeholder=False))
    db.add(PricingSkuPromo(sku_code="PPS2521010041011", taobao_item_id="9001",
                           taobao_sku_id="8001",
                           big_buyer_price=Decimal("3520.41"),      # 真锚
                           mid_buyer_price=Decimal("3520.41") * G_MIN,
                           enrolled_floor_price=Decimal("730")))    # ← 轮换遗留的脏底价
    db.commit()

    entries, stats = collect_signup_rows(db, "signup_price_big", lev=0.12)
    assert not (stats.get("anchor_smash_blocked") or [])
    got = {s.sku_code: value for s, _p, value in entries}
    assert got["PPS2521010041011"] == 5475.0


@pytest.mark.parametrize("anchor", ["153.06", "158.16", "183.67", "142.86", "20.41"])
def test_anchor_guard_not_tripped_by_own_safety_margin(db_session, anchor):
    """★护栏不能被【自己的安全垫】触发(2026-07-16 实抓 5 个 coupon_floor=None 的品被误拦):
    便宜品锚才 142~183, 1% 只有 1.4~1.8 元 < 合法让步(安全垫2 + 取整<1 ≈ 2.6) → 绝对容差必须
    = 安全垫+1, 不能写死 2.0。"""
    from app.services.data_export_service import collect_signup_rows
    db = db_session
    a = Decimal(anchor)
    db.add(PricingSku(product_code="PZ", sku_code="PPSTOLA01",
                      daily_price=a * 2, is_custom_placeholder=False))
    db.add(PricingSkuPromo(sku_code="PPSTOLA01", taobao_item_id="9500", taobao_sku_id="8500",
                           big_buyer_price=a, mid_buyer_price=a * G_MIN))   # 无 coupon_floor = 平台没给低线
    db.commit()
    entries, stats = collect_signup_rows(db, "signup_price_big", lev=0.12)
    blocked = stats.get("anchor_smash_blocked") or []
    assert not blocked, f"锚{a}: 合法安全垫被自己的护栏拦了 → {blocked}"
    assert any(s.sku_code == "PPSTOLA01" for s, _p, _v in entries)


def test_anchor_guard_allows_rounding_slip(db_session):
    """护栏不能误伤: 报名价取整到元的合法残差(<¥1, 占锚<0.15%)必须放行。"""
    from app.services.data_export_service import collect_signup_rows
    db = db_session
    db.add(PricingSku(product_code="P8", sku_code="PPS2525009040119",
                      daily_price=Decimal("14250"), is_custom_placeholder=False))
    db.add(PricingSkuPromo(sku_code="PPS2525009040119", taobao_item_id="9002",
                           taobao_sku_id="8002",
                           big_buyer_price=Decimal("9459.18"),      # 名义 9459.12, 让步仅 ¥0.06
                           mid_buyer_price=Decimal("9459.18") * G_MIN))
    db.commit()
    entries, stats = collect_signup_rows(db, "signup_price_big", lev=0.12)
    assert not (stats.get("anchor_smash_blocked") or []), "取整残差被误拦=护栏太紧"
    assert any(s.sku_code == "PPS2525009040119" for s, _p, _v in entries)


def test_never_concede_below_anchor(db_session):
    """真实SKU始终生成ERP日常价；资格冲突由预检整品暂缓，不能留给平台拒。"""
    from app.services.data_export_service import collect_signup_rows
    db = db_session
    db.add(PricingSku(product_code="PY", sku_code="PPSNOCON01",
                      daily_price=Decimal("30"), is_custom_placeholder=False))
    db.add(PricingSkuPromo(sku_code="PPSNOCON01", taobao_item_id="9600", taobao_sku_id="8600",
                           big_buyer_price=Decimal("20.41"),
                           mid_buyer_price=Decimal("20.41") * G_MIN,
                           coupon_floor_price=Decimal("16.38")))   # 线远低于锚
    db.commit()
    entries, stats = collect_signup_rows(db, "signup_price_big", lev=0.12)
    assert not (stats.get("no_concession_kept_erp_price") or [])
    got = {s.sku_code: v for s, _p, v in entries}
    assert got["PPSNOCON01"] == 30.0


def test_coupon_floor_tiny_drift_still_cannot_change_real_signup(db_session):
    """哪怕只差0.01元，真实SKU报名价也不得被历史线微调。"""
    from app.services.data_export_service import collect_signup_rows
    db = db_session
    db.add(PricingSku(product_code="PX", sku_code="PPSTINY01",
                      daily_price=Decimal("6000"), is_custom_placeholder=False))
    db.add(PricingSkuPromo(sku_code="PPSTINY01", taobao_item_id="9700", taobao_sku_id="8700",
                           big_buyer_price=Decimal("4275.51"),
                           mid_buyer_price=Decimal("4275.51") * G_MIN,
                           coupon_floor_price=Decimal("4274.71")))  # 只低 0.80
    db.commit()
    entries, stats = collect_signup_rows(db, "signup_price_big", lev=0.12)
    got = {s.sku_code: v for s, _p, v in entries}
    assert got["PPSTINY01"] == 6000.0


def test_placeholder_eats_coupon_floor():
    """★占位吃券后线: 治"3个占位(报500/线350)按'全SKU必须过'把8个真SKU整品拖垮"
    (2026-07-16 榉木岩板餐桌: 8个真SKU刚轮换、线全干净, 却因占位撞线整品被拒)。"""
    from app.services.data_export_service import _placeholder_signup_price
    sku = PricingSku(product_code="P1", sku_code="PPSTEST0197",
                     daily_price=Decimal("1000"), is_custom_placeholder=True)
    assert _placeholder_signup_price(sku, _promo(), 0.12) == 500.0
    # 线350 → 报名价 397 (397×0.88=349.36 ≤ 350 ✔; 398 就超: 350.24 > 350 —— 平台按精确值比)
    assert _placeholder_signup_price(sku, _promo(coupon_floor="350"), 0.12) == 397.0
    assert _platform_coupon(397) <= Decimal("350")
    assert _platform_coupon(398) > Decimal("350")
