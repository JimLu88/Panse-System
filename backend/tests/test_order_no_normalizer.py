"""order_no_normalizer 多规则平台订单号还原 单测 (规则用真实表验证过的样例)."""
from app.services.order_no_normalizer import resolve_platform_order_no, resolve_with_rule


def test_t200p_strip_prefix_and_space():
    # 企业号 9a: T200P + 中段 + 空格 + 尾段 → 拼接 (实测 418/432 命中订单表)
    assert resolve_platform_order_no("T200P2701846635029001 070") == "2701846635029001070"
    assert resolve_platform_order_no("T200P4502300400159017 902") == "4502300400159017902"


def test_raw_19_digit_passthrough():
    assert resolve_platform_order_no("3306036613231039079") == "3306036613231039079"


def test_provided_platform_column_wins():
    # 爱群号 9c 自带「平台订单号」列(19位) → 直接用, 即使关联订单号是支付码
    assert resolve_platform_order_no("P50578040792", provided="4999357982989757806") \
        == "4999357982989757806"


def test_unresolved_returns_none():
    # 无可靠规则的格式 → None (由调用方报异常待补规则), 绝不瞎猜错配
    for bad in (
        "2026042423001488331428798688",          # 爱群号 28 位
        "202604232000400111006 80090321541",     # 佳宝号 "日期 11位"
        "P2026052919111790990698043600",         # 主力号 P+长串
        "HJCAEB==5000000684700 86031==6470979",  # 9a 少数特殊
        "Empe8ep2Vr6qAVo+0+n/hYzIisvbyvH4",      # 乱码
        None, "", "   ",
    ):
        assert resolve_platform_order_no(bad) is None


def test_provided_non_19_digit_ignored():
    # 平台列不是 19 位 → 不采用, 继续看关联订单号规则
    assert resolve_platform_order_no("T200P2701846635029001 070", provided="abc") \
        == "2701846635029001070"


def test_rule_name_reported():
    assert resolve_with_rule("T200P2701846635029001 070")[1] == "企业号T200P"
    assert resolve_with_rule("3306036613231039079")[1] == "本身即19位"
    assert resolve_with_rule("x", provided="4999357982989757806")[1] == "平台订单号列直给"
    assert resolve_with_rule("nonsense###") == (None, None)
