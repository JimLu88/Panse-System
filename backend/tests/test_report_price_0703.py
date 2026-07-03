"""报名价模型 (2026-07-03 店铺宝失效 → 超级立减报名价; 大促锚不动, 只动中促)。

验证:
  报名价 A = 大促到手÷0.88; A中 = 中促到手÷0.90; 618报名价 = 大促到手÷0.85;
  合规 g = 中促到手÷大促到手 ≥ 0.90/0.88;
  微升中促: 不合规抬中促令 g=g_min, 大促价一分不动; 已合规不动。
"""
from __future__ import annotations

from decimal import Decimal as D

from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo
from app.services import pricing_calc_service as svc

# 佣金置 0 → 实收=到手, 便于手算核对
PARAMS = {"mid_vip_commission": D("0"), "big_vip_commission": D("0"),
          "mid_platform_discount": D("0.12"), "big_platform_discount": D("0.12")}


def _mk(daily, mid, big):
    sku = PricingSku(product_code="PPS1", sku_code="PPS1-A", sku="大号",
                     daily_price=D(str(daily)), mid_promo=D(str(mid)), big_promo=D(str(big)))
    promo = PricingSkuPromo(sku_code="PPS1-A")
    svc.recompute_promo(promo, sku, PARAMS)
    return sku, promo


def test_report_price_A_from_big():
    # 大促实收880, 佣金0 → 大促到手880 → 报名价A = 880/0.88 = 1000; 618 = 880/0.85 = 1035
    _, promo = _mk(2000, 900, 880)
    rp = svc.report_prices(promo, PARAMS)
    assert rp["report_price"] == D("1000")
    assert rp["report_price_618"] == D("1035")  # 880/0.85=1035.29 → 1035


def test_compliant_when_ratio_is_0227():
    # 中促900/大促880 = 1.02273 = g_min → 合规, A中 = 900/0.90 = 1000 = A
    _, promo = _mk(2000, 900, 880)
    rp = svc.report_prices(promo, PARAMS)
    assert rp["report_compliant"] is True
    assert rp["report_price_mid"] == rp["report_price"]           # A中 == A
    assert rp["gap_floor"] == D("900.00")                          # 空档价红线 = 中促到手


def test_broken_when_mid_too_low():
    # 中促880 = 大促880 → g=1.0 < 1.02273 → 不合规
    _, promo = _mk(2000, 880, 880)
    rp = svc.report_prices(promo, PARAMS)
    assert rp["report_compliant"] is False
    assert rp["compliance_g"] == D("1.000000")


def test_fix_raises_mid_big_untouched():
    # 不合规: 中促880 大促880 → 微升中促至 大促到手×0.90/0.88 = 900; 大促一分不动
    sku, _ = _mk(2000, 880, 880)
    big_before = sku.big_promo
    r = svc.fix_mid_to_compliant(sku, PARAMS)
    assert r is not None
    assert sku.big_promo == big_before                            # ★ 大促价零变动
    assert sku.mid_promo == D("900.00")                           # 中促抬到 900
    assert sku.base_mid is None                                    # 清基数, recompute 不覆盖
    assert r["mid_before"] == 880.0 and r["mid_after"] == 900.0
    # 修复后再算, g 应达标
    promo2 = PricingSkuPromo(sku_code=sku.sku_code)
    svc.recompute_promo(promo2, sku, PARAMS)
    rp = svc.report_prices(promo2, PARAMS)
    assert rp["report_compliant"] is True


def test_fix_noop_when_compliant():
    # 已合规(900/880) → 不动, 返回 None
    sku, _ = _mk(2000, 900, 880)
    mid_before, big_before = sku.mid_promo, sku.big_promo
    r = svc.fix_mid_to_compliant(sku, PARAMS)
    assert r is None
    assert sku.mid_promo == mid_before and sku.big_promo == big_before


def test_recompute_keeps_fixed_mid():
    # 微升后 base_mid=None → recompute() 不该把中促覆盖回去
    sku, _ = _mk(2000, 880, 880)
    svc.fix_mid_to_compliant(sku, PARAMS)
    svc.recompute(sku)
    assert sku.mid_promo == D("900.00")                            # 保住手改值
    assert sku.big_promo == D("880")                              # 大促仍不动
