"""跨品牌产品编码归并 (同一实物在 PPS/PFG 两品牌, 数字主体一致) 单测。"""
from app.services.product_coder import brand_variants, core_of


def test_core_strips_brand_prefix():
    # 同一实物在两个品牌 + 订单去品牌形式 → 数字主体一致
    assert core_of("PPS26380040225") == "26380040225"
    assert core_of("PFG26380040225") == "26380040225"
    assert core_of("P26380040225") == "26380040225"   # 订单商家编码(去品牌)


def test_core_on_sku_code():
    # SKU 编码 (含尾部 2 位 SKU 号) 同样归并
    assert core_of("PPS2638004022511") == "2638004022511"
    assert core_of("PFG2638004022511") == "2638004022511"


def test_core_empty():
    assert core_of(None) is None
    assert core_of("") is None
    assert core_of("   ") is None


def test_brand_variants_covers_both_brands_and_order_form():
    v = brand_variants("PFG26380040225")
    assert "PPS26380040225" in v     # 畔色
    assert "PFG26380040225" in v     # 孚格
    assert "P26380040225" in v       # 订单去品牌形式


def test_brand_variants_from_order_code():
    # 给订单形式 P{X} 也能还原出两品牌编码 (用于按物理实物归并)
    v = brand_variants("P26380040225")
    assert {"PPS26380040225", "PFG26380040225", "P26380040225"} <= v


def test_brand_variants_empty():
    assert brand_variants(None) == set()
