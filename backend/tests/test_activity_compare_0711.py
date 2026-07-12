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
    # 撤销重报口径(2026-07-12): 占位活动价 = 占位报名价 = 现价×0.9 = 2700, 补贴 = 活动价×0.1 = 270。
    # 注意: 270 与史前 bug "现价×0.09" 数值巧合相同, 但推导完全不同 —— 现在活动价列也填 2700,
    # 三列(活动价/让利10%/补贴)自洽, 到手 = 2430; 旧 bug 是补贴单飞无活动价。
    ph = [r for r in rows if r["sku_code"] == "PPS1001099"]
    assert ph, "占位符应在超级立减比对表里"
    assert abs(ph[0]["system_value"] - 270.0) < 0.01, \
        f"占位符补贴应=270(占位报名价2700×0.1), 实得 {ph[0]['system_value']}"
    assert abs(ph[0]["target_shoudao"] - 2430.0) < 0.01, "占位到手应=活动价×0.9"


def test_super_reduce_fresh_signup_fills_three_columns(db_session):
    """撤销全部报名后重新报名是全新报名: 活动价(C)/让利比例(M)/补贴金额(N) 三列必须全填且自洽,
    包邮(E)填'包邮'。旧版只填补贴金额会被平台以活动价必填拒收。"""
    import io
    import openpyxl
    _seed(db_session)
    xlsx, _ = up._gen_xlsx(db_session, "super_reduce", "big")
    wb = openpyxl.load_workbook(io.BytesIO(xlsx), read_only=True, data_only=True)
    ws = wb.worksheets[0]
    n = 0
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or row[1] is None:
            continue
        n += 1
        act, free_ship, ratio, subsidy = row[2], row[4], row[12], row[13]
        assert act and float(act) > 0, f"SKUID {row[1]} 活动价未填"
        assert free_ship == "包邮", f"SKUID {row[1]} 包邮未填"
        assert ratio == 10, f"SKUID {row[1]} 让利比例应=10, 实得 {ratio}"
        assert abs(float(subsidy) - round(float(act) * 0.1, 2)) < 0.01, \
            f"SKUID {row[1]} 补贴({subsidy})≠活动价({act})×10%"
    wb.close()
    assert n >= 5, "应含正常品+alt+占位的全部行"


def test_single_item_mid_tier_uses_mid_deduct_not_big(db_session):
    """回归: single_item tier=mid 必须用 mid_deduct, 不能死写 big_deduct(否则比对表假核对)。"""
    _seed(db_session)
    rows_mid = {r["sku_code"]: r["system_value"] for r in up._compare_rows(db_session, "single_item_discount", "mid")}
    rows_big = {r["sku_code"]: r["system_value"] for r in up._compare_rows(db_session, "single_item_discount", "big")}
    # 正常品 mid/big 立减金额应不同(中促力度10% vs 大促力度12%, 且到手不同)
    sc = "PPS1001011"
    assert sc in rows_mid and sc in rows_big
    assert rows_mid[sc] != rows_big[sc], "mid 档立减金额不应等于 big 档(证明没死写 big_deduct)"
