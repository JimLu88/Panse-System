"""活动价倒推引擎 (2026-07-02 用户改回 Excel 方式): 输入店铺实收(小/中/大促价) → 反推店铺宝系数。

口径复刻用户 Excel「活动价」表 Q/U/AB:
  小促: 系数 = 小促价 ÷ 日常
  中促/大促: 买家到手 = 实收 ÷ (1−佣金); 系数 = 买家到手 ÷ (日常 ×(1−立减)); 店铺到手 = 实收; VIP到手 = 买家 − 消费券
"""
from decimal import Decimal as D

from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo
from app.services.pricing_calc_service import PROMO_PARAM_DEFAULTS, recompute_promo


def _params():
    return {k: D(v) for k, v in PROMO_PARAM_DEFAULTS.items()}


def test_promo_reverse_from_shop_receipt_price():
    sku = PricingSku(sku_code="T1", product_code="P1", daily_price=D("1000"),
                     small_promo=D("680"), mid_promo=D("678"), big_promo=D("647"))
    promo = PricingSkuPromo(sku_code="T1")
    recompute_promo(promo, sku, _params())

    # 小促: 无立减/佣金 → 买家到手 = 店铺实收 = 小促价; 系数 = 小促价 ÷ 日常
    assert promo.shop_internal_final == D("680")
    assert abs(promo.shop_promo_rate - D("0.68")) < D("0.000001")

    # 中促: 佣金1% → 买家到手 = 678 ÷ 0.99; 店铺到手 = 678(实收); 系数 = 买家 ÷ (1000 × 0.88)
    buyer_mid = (D("678") / D("0.99")).quantize(D("0.01"))
    assert promo.mid_buyer_price == buyer_mid
    assert promo.mid_shop_receipt == D("678")
    assert abs(promo.mid_shop_rate - (buyer_mid / (D("1000") * D("0.88")))) < D("0.00001")
    # VIP到手 = 买家 − 消费券(≥500 减 50)
    assert promo.mid_vip_final == buyer_mid - D("50")

    # 大促: 佣金0% → 买家到手 = 647; 系数 = 647 ÷ 880
    assert promo.big_buyer_price == D("647.00")
    assert promo.big_shop_receipt == D("647")
    assert abs(promo.big_shop_rate - (D("647") / (D("1000") * D("0.88")))) < D("0.00001")
    assert promo.big_vip_final == D("647") - D("50")


def test_promo_reverse_guards_zero_daily():
    sku = PricingSku(sku_code="T2", product_code="P2", daily_price=D("0"), big_promo=D("500"))
    promo = PricingSkuPromo(sku_code="T2")
    recompute_promo(promo, sku, _params())     # 日常价为 0 → 直接返回, 不抛、不写系数
    assert promo.big_shop_rate is None


def test_promo_reverse_skips_missing_tier():
    sku = PricingSku(sku_code="T3", product_code="P3", daily_price=D("1000"),
                     big_promo=D("647"))         # 只有大促价, 无小/中促价
    promo = PricingSkuPromo(sku_code="T3")
    recompute_promo(promo, sku, _params())
    assert promo.shop_promo_rate is None         # 小促无价 → 不反推
    assert promo.mid_shop_rate is None
    assert promo.big_shop_rate is not None        # 大促有价 → 反推
