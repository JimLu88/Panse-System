"""单品立减 加法口径 (2026-07-06 用户附图核准)。

淘宝官方大促实际算法 = 官方立减 + 单品补贴 两个折扣【从活动价各自减】(加法, 非乘法):
  到手 = 活动价 − 活动价×官方力度 − 单品立减金额   (活动价 = 日常价 = 标价×0.75)
  → 单品立减折 = 到手 ÷ 日常价 + 官方力度 ;  立减金额 = 日常价×(1−官方力度) − 到手

真实附图 1.2 米黑胡桃柜: 活动价(=日常价)19575, 官方立减12%(=2349), 目标大促买家价 13153。
同事误按乘法/填 8.1 折 → 单品补贴仅 3719 → 到手 13506.75, 比目标高 353.69。
正确应填 ≈7.92 折 / 立减 4073 元 → 到手 13153。

三档场次(官方力度, 目标到手): 中促(日常 10% → 中促买家价) / 大促(88VIP 12% → 大促买家价) /
超大促(618·双11 15% → 大促买家价, 同价换 SKU)。
"""
from __future__ import annotations

from decimal import Decimal as D

from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo
from app.services import pricing_calc_service as svc

# 佣金 2% (对齐 NAS live: mid=big=0.02); 力度用报名口径 中促10/大促12/618 15
PARAMS = {"mid_vip_commission": D("0.02"), "big_vip_commission": D("0.02"),
          "mid_platform_discount": D("0.12"), "big_platform_discount": D("0.12")}


def _mk(daily, big, mid=None):
    sku = PricingSku(product_code="PPS", sku_code="PPS-A", sku="1.2米",
                     daily_price=D(str(daily)), big_promo=D(str(big)),
                     mid_promo=D(str(mid)) if mid is not None else None)
    promo = PricingSkuPromo(sku_code="PPS-A")
    svc.recompute_promo(promo, sku, PARAMS)
    return sku, promo


def test_additive_matches_taobao_screenshot():
    # 大促价(店铺实收)12890 → 大促买家价 = 12890/0.98 = 13153.06
    sku, promo = _mk(19575, 12890)
    assert abs(float(promo.big_buyer_price) - 13153.06) < 0.5
    d = svc.single_item_discounts(promo, sku.daily_price, PARAMS)
    # 大促(12%): 折 = 13153.06/19575 + 0.12 = 0.7920; 立减 = 19575*0.88 − 13153.06 = 4072.94
    assert abs(float(d["big_discount"]) - 0.7920) < 0.001
    assert abs(float(d["big_deduct"]) - 4072.94) < 1.0
    # 超大促 618(15%): 折 = +0.15 = 0.8220; 立减 = 19575*0.85 − 13153.06 = 3485.69
    assert abs(float(d["big618_discount"]) - 0.8220) < 0.001
    assert abs(float(d["big618_deduct"]) - 3485.69) < 1.0


def test_deduct_reaches_target_to_hand():
    # 用算出的立减金额按【加法】反推到手, 必须 = 目标大促买家价 (闭环)
    sku, promo = _mk(19575, 12890)
    d = svc.single_item_discounts(promo, sku.daily_price, PARAMS)
    daily = D("19575")
    to_hand = daily - daily * D("0.12") - D(str(d["big_deduct"]))       # 到手 = 日常 − 官方12% − 立减
    assert abs(float(to_hand) - float(promo.big_buyer_price)) < 0.5


def test_618_is_3pct_shallower_than_big():
    # 同价换 SKU: 618 官方力度深 3 个点(15 vs 12) → 单品立减折 恰好 +0.03(打得浅)
    sku, promo = _mk(19575, 12890)
    d = svc.single_item_discounts(promo, sku.daily_price, PARAMS)
    assert abs((float(d["big618_discount"]) - float(d["big_discount"])) - 0.03) < 1e-6


def test_mid_uses_10pct_and_mid_target():
    # 中促(日常场)用 10% 力度 + 中促买家价; 折 = 中促买家价/日常 + 0.10
    # ★任务#22: 中促买家价 = 大促买家价 × 1.03 = 13153.06×1.03 = 13547.65 (sku.mid_promo=13183 不再参与)
    sku, promo = _mk(19575, 12890, mid=13183)
    assert promo.mid_buyer_price == D("13547.65")
    d = svc.single_item_discounts(promo, sku.daily_price, PARAMS)
    exp = float(promo.mid_buyer_price) / 19575 + 0.10
    assert abs(float(d["mid_discount"]) - exp) < 0.001


def test_official_discount_exceeds_target_flags_none():
    # 浅折 SKU: 目标到手接近日常, 官方立减 12% 已够 → 折>1 → None(该档不适用, 不给假数)
    sku, promo = _mk(100, 95)          # 大促买家价 96.94; 96.94/100+0.12 = 1.089 > 1
    d = svc.single_item_discounts(promo, sku.daily_price, PARAMS)
    assert d["big_discount"] is None and d["big_deduct"] is None


def test_no_daily_returns_empty():
    sku = PricingSku(product_code="P", sku_code="P-Z", daily_price=None, big_promo=D("100"))
    promo = PricingSkuPromo(sku_code="P-Z")
    d = svc.single_item_discounts(promo, sku.daily_price, PARAMS)
    assert d["big_discount"] is None
