"""上传比对表严格核对: 「上传值」(真正要传千牛的 xlsx 里的数) == 「系统应填值」(_compare_rows 独立重算),
三渠道 × 各档位 × 定制占位符 全部零漂移。用户 2026-07-11: 必须按系统价, 比对表要真核对上传价 vs 系统价。

历史暗坑(本测试锁死回归): _compare_rows 曾死写 big_deduct/report_price、占位符补贴误算 ×0.09,
在 tier≠big / 618 档 / 超级立减占位符 时算出跟真实上传不一样的数 → 比对表"核对"变假。
"""
from decimal import Decimal

from app.models.pricing import PricingSku
from app.models.pricing_ext import PricingSkuPromo
from app.services import activity_upload_service as up


def _add(db, pc, sc, name, daily, item, sid, big_buyer=None, mid_buyer=None, ph=False, alt=None):
    db.add(PricingSku(product_code=pc, sku_code=sc, sku=name, product_name=name,
                      daily_price=Decimal(str(daily)), is_custom_placeholder=ph))
    db.add(PricingSkuPromo(sku_code=sc, taobao_item_id=item, taobao_sku_id=sid,
                           alt_taobao_sku_ids=alt,
                           big_buyer_price=Decimal(str(big_buyer)) if big_buyer else None,
                           mid_buyer_price=Decimal(str(mid_buyer)) if mid_buyer else None))


def _seed(db):
    # 各尺寸不同价的正常品 (避免被"坏价雷同"排除); 011 挂一码多SKU(alt) 验证比对表覆盖 alt
    _add(db, "PPS1001", "PPS1001011", "软包床-1.5米", 2600, "111", "600001", big_buyer=2000, mid_buyer=2046,
         alt=["600001A", "600001B"])
    _add(db, "PPS1001", "PPS1001012", "软包床-1.8米", 2860, "111", "600002", big_buyer=2200, mid_buyer=2250)
    _add(db, "PPS2001", "PPS2001011", "餐桌-1.4米", 3000, "222", "600003", big_buyer=2300, mid_buyer=2352)
    # 定制占位符(补贴/报名价走 ×0.1 / ×0.9 特殊口径)
    _add(db, "PPS1001", "PPS1001099", "尺寸定制", 3000, "111", "600099", ph=True)
    db.commit()


def test_alt_skuids_fully_covered_in_compare(db_session):
    """回归(工作流对抗验证抓出): 一码多SKU 的 alt SKUID 也必须逐行进比对表, 与真 xlsx SKUID 集合完全一致,
    否则 alt 被真上传却在人工核对闸门里查无此行(commit 不可逆)。"""
    _seed(db_session)
    for channel in ("single_item_discount", "promo_signup", "super_reduce"):
        xlsx, _ = up._gen_xlsx(db_session, channel, "big")
        xlsx_ids = set(up._parse_uploaded_values(channel, xlsx))
        compare_ids = {r["taobao_sku_id"] for r in up._compare_rows(db_session, channel, "big")}
        assert compare_ids == xlsx_ids, f"{channel} 比对表SKUID≠上传SKUID: 漏={xlsx_ids - compare_ids}"
        assert "600001A" in compare_ids and "600001B" in compare_ids, f"{channel} alt 未进比对表"


def _mismatches(db, channel, tier):
    """复刻 stage() 的核对: 生成真 xlsx → 解析上传值 → 对比 _compare_rows 系统应填值。"""
    xlsx, _ = up._gen_xlsx(db, channel, tier)
    uploaded = up._parse_uploaded_values(channel, xlsx)
    rows = up._compare_rows(db, channel, tier)
    bad = []
    for r in rows:
        u = uploaded.get(r["taobao_sku_id"])
        s = r.get("system_value")
        if u is None or s is None or abs(u - s) > up._PRICE_MATCH_EPS:
            bad.append((r["sku_code"], u, s))
    return rows, bad


def test_single_item_all_tiers_zero_drift(db_session):
    _seed(db_session)
    for tier in ("mid", "big", "big618"):
        rows, bad = _mismatches(db_session, "single_item_discount", tier)
        assert rows, f"single_item {tier} 应有行"
        assert not bad, f"single_item {tier} 上传值≠系统应填值: {bad}"


def test_promo_signup_all_tiers_zero_drift(db_session):
    _seed(db_session)
    for tier in ("mid", "big", "big618"):
        rows, bad = _mismatches(db_session, "promo_signup", tier)
        assert rows, f"promo_signup {tier} 应有行"
        assert not bad, f"promo_signup {tier} 上传值≠系统应填值: {bad}"


def test_super_reduce_zero_drift_and_placeholder_x01(db_session):
    _seed(db_session)
    rows, bad = _mismatches(db_session, "super_reduce", "big")
    assert rows, "super_reduce 应有行"
    assert not bad, f"super_reduce 上传值≠系统应填值: {bad}"
    # 占位符补贴 = 现价 × 0.1 = 300 (锁死: 绝不是旧版误算 ×0.09 = 270)
    ph = [r for r in rows if r["sku_code"] == "PPS1001099"]
    assert ph, "占位符应在超级立减比对表里"
    assert abs(ph[0]["system_value"] - 300.0) < 0.01, f"占位符补贴应=300(现价×0.1), 实得 {ph[0]['system_value']}"


def test_single_item_mid_tier_uses_mid_deduct_not_big(db_session):
    """回归: single_item tier=mid 必须用 mid_deduct, 不能死写 big_deduct(否则比对表假核对)。"""
    _seed(db_session)
    rows_mid = {r["sku_code"]: r["system_value"] for r in up._compare_rows(db_session, "single_item_discount", "mid")}
    rows_big = {r["sku_code"]: r["system_value"] for r in up._compare_rows(db_session, "single_item_discount", "big")}
    # 正常品 mid/big 立减金额应不同(中促力度10% vs 大促力度12%, 且到手不同)
    sc = "PPS1001011"
    assert sc in rows_mid and sc in rows_big
    assert rows_mid[sc] != rows_big[sc], "mid 档立减金额不应等于 big 档(证明没死写 big_deduct)"
