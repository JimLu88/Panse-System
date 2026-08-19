"""任务#22 (2026-07-17 用户拍板: "现在先把标准定调为 1.03，后续系统里也同步改掉"):
中促价统一 = 大促价 × 1.03 固化进 ERP 定价链 + 无动销到手精确等于 ERP 中促价。

锁五件事:
① recompute_promo: mid_buyer_price = round(big_buyer × K, 2), K 默认 1.03 —— 全链唯一来源
② K 可配(promo_mid_over_big_ratio) + 低于数学下限(0.90/0.88≈1.0227)顶回
③ 与 campaign_service.mid_buyer_inplace(活动引擎就地×1.03) 交叉一致 —— 两套系统同口径
④ backfill_mid_buyer: 全店旧漂移值(1.025~1.038)一次性归一到 big×1.03; sku 四档价一分不动
⑤ 无动销 builder(日常−中促) 与 campaign_service._nosales_discount_row 数值一致
"""
from decimal import Decimal as D

from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo
from app.services import campaign_service
from app.services import data_export_service as de
from app.services import no_sales_service as ns
from app.services import pricing_calc_service as pcs


def _pair(daily, big, mid_promo=None, params=None):
    sku = PricingSku(product_code="PU1", sku_code="PU1-A", sku="1.2米",
                     daily_price=D(str(daily)), big_promo=D(str(big)),
                     mid_promo=D(str(mid_promo)) if mid_promo is not None else None)
    promo = PricingSkuPromo(sku_code="PU1-A")
    pcs.recompute_promo(promo, sku, params)
    return sku, promo


# ── ① 唯一来源: mid_buyer = big_buyer × 1.03 ─────────────────────────────────
def test_mid_buyer_is_big_times_103_default():
    # 默认参数(佣金: 中促1% / 大促0%): big_buyer = 880; mid_buyer = 880×1.03 = 906.40
    _, promo = _pair(2000, 880, mid_promo=900)
    assert promo.big_buyer_price == D("880.00")
    assert promo.mid_buyer_price == D("906.40")
    # 店铺到手 = 到手 × (1−佣金1%) = 906.40×0.99 = 897.34 (不再等于 sku.mid_promo)
    assert promo.mid_shop_receipt == D("897.34")


def test_mid_buyer_odd_cents_rounding():
    # 分位大促锚: 2561.22×1.03 = 2638.0566 → round 2 位 = 2638.06
    _, promo = _pair(3000, "2561.22")
    assert promo.mid_buyer_price == D("2638.06")


# ── ② K 旋钮可配 + 下限钳制 ───────────────────────────────────────────────────
def test_ratio_knob_configurable():
    _, promo = _pair(2000, 880, params={"mid_over_big_ratio": "1.05"})
    assert promo.mid_buyer_price == D("924.00")            # 880×1.05


def test_ratio_below_math_floor_clamped():
    # K=1.00 < 数学下限 0.90/0.88 → 顶回下限: 880×1.022727… = 900.00
    _, promo = _pair(2000, 880, params={"mid_over_big_ratio": "1.00"})
    assert promo.mid_buyer_price == D("900.00")


def test_default_constant_is_103():
    assert pcs._MID_OVER_BIG_RATIO == D("1.03")
    assert pcs.PROMO_PARAM_DEFAULTS["mid_over_big_ratio"] == "1.03"


# ── ③ 与 campaign_service(活动引擎就地×1.03) 交叉一致 ─────────────────────────
def test_cross_consistent_with_campaign_service():
    """两套系统同口径钉死: ERP 定价链落库值 == 活动引擎就地计算值, 逐分一致。"""
    assert campaign_service.MID_OVER_BIG_RATIO == pcs._MID_OVER_BIG_RATIO == D("1.03")
    for big in ("880", "2561.22", "4275.51", "13153.06", "20.41"):
        _, promo = _pair(30000, big)
        assert promo.mid_buyer_price == campaign_service.mid_buyer_inplace(promo), big


# ── ④ backfill: 旧漂移一次性归一, sku 四档价零变动 ────────────────────────────
def test_backfill_mid_buyer_normalizes_drift(db_session):
    def _seed(code, daily, big, old_mid_buyer):
        db_session.add(PricingSku(product_code=code[:8], sku_code=code, sku="s",
                                  daily_price=D(str(daily)),
                                  big_promo=D(str(big)) if big is not None else None))
        db_session.add(PricingSkuPromo(
            sku_code=code, big_buyer_price=D(str(big)) if big is not None else None,
            mid_buyer_price=D(str(old_mid_buyer)) if old_mid_buyer is not None else None))

    _seed("PPSBF001", 2000, 880, "902.00")      # 旧漂移 1.025 → 应归一 906.40
    _seed("PPSBF002", 3000, "2561.22", "2658.55")  # 旧漂移 ~1.038 → 应归一 2638.06
    _seed("PPSBF003", 2000, 880, "906.40")      # 已是新口径 → unchanged
    _seed("PPSBF004", 2000, None, "700.00")     # 无大促价 → 唯一来源算不出, 原值保留
    db_session.commit()

    stats = pcs.backfill_mid_buyer(db_session)

    assert stats["ratio"] == 1.03
    assert stats["scanned"] == 4
    assert stats["changed"] == 2 and stats["unchanged"] == 1
    assert stats["skipped_no_big"] == 1

    got = {p.sku_code: p for p in db_session.query(PricingSkuPromo).all()}
    assert got["PPSBF001"].mid_buyer_price == D("906.40")
    assert got["PPSBF002"].mid_buyer_price == D("2638.06")
    assert got["PPSBF003"].mid_buyer_price == D("906.40")
    assert got["PPSBF004"].mid_buyer_price == D("700.00")   # 原值保留
    # ★铁律: backfill 只动 promo 派生链, sku 四档价一分不动
    skus = {s.sku_code: s for s in db_session.query(PricingSku).all()}
    assert skus["PPSBF001"].big_promo == D("880") and skus["PPSBF001"].daily_price == D("2000")
    assert skus["PPSBF002"].big_promo == D("2561.22")


# ── ⑤ 无动销: builder 与 campaign 引擎数值一致 (到手 = ERP 中促价) ─────────────
def test_nosales_builder_matches_campaign_row(db_session):
    db_session.add(PricingSku(product_code="PPSNU01", sku_code="PPSNU011", sku="s",
                              daily_price=D("3000")))
    db_session.add(PricingSkuPromo(sku_code="PPSNU011", taobao_item_id="9901",
                                   taobao_sku_id="77001",
                                   big_buyer_price=D("2561.22"),
                                   mid_buyer_price=D("2638.06")))   # = 2561.22×1.03 (backfill 后)
    db_session.commit()
    ns.add_no_sales(db_session, ["9901"])

    # data_export builder: 立减 = 日常 − ERP中促到手 = 3000 − 2638.06 = 361.94
    import io
    import openpyxl
    bio, stats = de.build_nosales_single_item_discount_xlsx(db_session)
    ws = openpyxl.load_workbook(io.BytesIO(bio.getvalue())).active
    assert stats["rows"] == 1
    builder_deduct = float(ws.cell(2, 3).value)

    # campaign 引擎同一 SKU 的无动销行
    s = db_session.query(PricingSku).filter_by(sku_code="PPSNU011").one()
    p = db_session.query(PricingSkuPromo).filter_by(sku_code="PPSNU011").one()
    row = campaign_service._nosales_discount_row(
        s, p, {"skipped_no_target": 0, "skipped_no_deduct": 0})
    assert row is not None
    assert builder_deduct == row["deduct"] == 361.94
    assert row["target_price"] == 2638.06                    # 到手 = ERP中促价
