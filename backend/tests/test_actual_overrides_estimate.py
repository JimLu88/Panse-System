"""实际账单覆盖预估 — physical_cost 替换逻辑测试 (用户 2026-06-21)。

成本 = 原成本 − 预估 + 实际(只换精确配到逐单的分量); 未配/无预估保持原样; 防双减。
"""
from decimal import Decimal

from app.models.order import Order
from app.services.order_financials import physical_cost


def _o(**kw):
    kw.setdefault("platform", "淘宝")
    kw.setdefault("order_no", "X")
    kw.setdefault("qty", 1)
    kw.setdefault("paid_amount", Decimal("9999"))  # 高实付, 不触发片段封顶
    return Order(**kw)


def test_packing_actual_replaces_estimate(db_session):
    o = _o(theoretical_cost=Decimal("1000"), est_packing=Decimal("100"), actual_packing=Decimal("150"))
    assert physical_cost(o) == Decimal("1050")   # 1000 − 100 + 150


def test_logistics_actual_replaces_estimate(db_session):
    o = _o(theoretical_cost=Decimal("1000"), est_logistics=Decimal("200"), actual_logistics=Decimal("180"))
    assert physical_cost(o) == Decimal("980")     # 1000 − 200 + 180


def test_both_swapped(db_session):
    o = _o(theoretical_cost=Decimal("1000"),
           est_packing=Decimal("100"), actual_packing=Decimal("150"),
           est_logistics=Decimal("200"), actual_logistics=Decimal("180"))
    assert physical_cost(o) == Decimal("1030")    # 1000 −100+150 −200+180


def test_install_actual_replaces_estimate(db_session):
    o = _o(theoretical_cost=Decimal("1000"), est_install=Decimal("100"), actual_install=Decimal("120"))
    assert physical_cost(o) == Decimal("1020")     # 1000 − 100 + 120


def test_unmatched_keeps_estimate(db_session):
    """没有 actual(没配到)→ 不替换, 保持预估总成本。"""
    o = _o(theoretical_cost=Decimal("1000"), est_packing=Decimal("100"))
    assert physical_cost(o) == Decimal("1000")


def test_install_est_actual_sync(db_session):
    """安装: est=定价表 install_cost×qty, actual=订单 install_fee+upstairs_fee。"""
    from app.models.pricing import PricingSku
    from app.services import order_fee_actual_service as svc
    db_session.add(PricingSku(product_code="PI", sku_code="PPS1111111110011",
                              install_cost=Decimal("150")))
    db_session.add(Order(platform="淘宝", order_no="I1", qty=1, status="signed",
                         paid_amount=Decimal("3000"), sku_code="PPS1111111110011",
                         install_fee=Decimal("130"), upstairs_fee=Decimal("20")))
    db_session.flush()
    svc.sync_fee_components(db_session)
    o = db_session.query(Order).filter_by(order_no="I1").first()
    assert o.est_install == Decimal("150")
    assert o.actual_install == Decimal("150")   # 130 + 20


def test_estimate_fee_product_fallback_when_sku_missing():
    """sku_code 缺失 → 用 product_code 兜底取该产品兄弟SKU中位数, 不落全局中位 (用户 2026-06-28)。

    根因: sku_code=None 的单, est 落全局中位、而 theoretical 按 product_code 命中定价表 →
    两者口径不一致 → physical_cost swap 基线错、有实际账单时把物流/安装多算。"""
    from app.services.order_fee_actual_service import estimate_fee
    by_sku: dict = {}   # 定价表查不到该 sku
    base_maps = {"pk": {"PPS24250080801": Decimal("450")},
                 "lg": {"PPS24250080801": Decimal("750")},
                 "inst": {"PPS24250080801": Decimal("250")}, "phys": {}}
    # 有 product_code 兜底 → 取该产品中位数
    assert estimate_fee(None, by_sku, base_maps, fallback_base="PPS24250080801") == (
        Decimal("450"), Decimal("750"), Decimal("250"))
    # 旧行为(无兜底)→ 落空, 由上层全局中位再兜
    assert estimate_fee(None, by_sku, base_maps) == (None, None, None)


def test_no_estimate_no_swap(db_session):
    """有 actual 但无 est(定价表缺)→ 不替换(防乱减), 保持原样。"""
    o = _o(theoretical_cost=Decimal("1000"), actual_packing=Decimal("150"))
    assert physical_cost(o) == Decimal("1000")


def test_factory_actual_cost_with_wood_est_swaps(db_session):
    """工厂账单单(actual_cost=木作) + wood_est 补非木作 → 预估在 cost 里, 可替换。"""
    o = _o(actual_cost=Decimal("700"), wood_cost_est=Decimal("700"),
           theoretical_cost=Decimal("1000"),   # 非木作=300(含预估打包100)
           est_packing=Decimal("100"), actual_packing=Decimal("150"))
    # cost = 700 + (1000-700) = 1000; 换打包 −100+150 = 1050
    assert physical_cost(o) == Decimal("1050")


def test_factory_actual_cost_no_wood_est_no_swap(db_session):
    """工厂账单单但无 wood_est(没补非木作)→ 非木作预估不进 cost、不替换(防双减);
    但打包费仍计入(第16条: physical_cost 本就含打包, actual_cost 仅木作)。"""
    o = _o(actual_cost=Decimal("700"),
           est_packing=Decimal("100"), actual_packing=Decimal("150"))
    assert physical_cost(o) == Decimal("850")   # 700 木作 + 150 实际打包(不补非木作, 但含打包)


def test_fragment_cap_after_swap(db_session):
    """片段封顶: 替换后再判定 实付<成本50% → 实付×85%。"""
    o = _o(theoretical_cost=Decimal("1000"), paid_amount=Decimal("100"),
           est_logistics=Decimal("200"), actual_logistics=Decimal("180"))
    # swap → 980; 实付100 < 980×50% → 100×0.85 = 85
    assert physical_cost(o) == Decimal("85.00")


def test_custom_sku_estimate_uses_base_product_median(db_session):
    """定制 SKU(尾号≥90)定价表无此行 → 按基础产品码兄弟 SKU 中位数取预估(用户 2026-06-21)。"""
    from app.models.pricing import PricingSku
    from app.services import order_fee_actual_service as svc
    db_session.add(PricingSku(product_code="P1", sku_code="PPS2421007090113",
                              packaging_cost=Decimal("180"), logistics_cost=Decimal("280")))
    db_session.add(PricingSku(product_code="P1", sku_code="PPS2421007090115",
                              packaging_cost=Decimal("120"), logistics_cost=Decimal("300")))
    db_session.add(Order(platform="淘宝", order_no="C1", qty=1, status="signed",
                         paid_amount=Decimal("5000"), sku_code="PPS2421007090199"))  # 定制尾号99
    db_session.flush()
    svc.sync_fee_components(db_session)
    o = db_session.query(Order).filter_by(order_no="C1").first()
    assert o.est_packing == Decimal("150")     # 中位数[120,180]
    assert o.est_logistics == Decimal("290")   # 中位数[280,300]


def test_exact_sku_estimate_preferred_over_base(db_session):
    """精确 SKU 有费用 → 优先用它, 不走基础产品码兜底。"""
    from app.models.pricing import PricingSku
    from app.services import order_fee_actual_service as svc
    db_session.add(PricingSku(product_code="P2", sku_code="PPS2421007090113",
                              packaging_cost=Decimal("180"), logistics_cost=Decimal("280")))
    db_session.add(Order(platform="淘宝", order_no="N1", qty=2, status="signed",
                         paid_amount=Decimal("5000"), sku_code="PPS2421007090113"))
    db_session.flush()
    svc.sync_fee_components(db_session)
    o = db_session.query(Order).filter_by(order_no="N1").first()
    assert o.est_packing == Decimal("360")     # 180 × qty2
    assert o.est_logistics == Decimal("560")   # 280 × qty2


def test_ratio_fallback_for_no_sku_order(db_session):
    """无 SKU/产品 预估的单(差价/邮费专链)→ 系统平均比例(预估÷实付)× 本单实付。"""
    from app.models.pricing import PricingSku
    from app.services import order_fee_actual_service as svc
    db_session.add(PricingSku(product_code="P3", sku_code="PPS9999999990011",
                              packaging_cost=Decimal("100"), logistics_cost=Decimal("200")))
    db_session.add(Order(platform="淘宝", order_no="R0", qty=1, status="signed",
                         paid_amount=Decimal("1000"), sku_code="PPS9999999990011"))  # ratio 0.1/0.2
    db_session.add(Order(platform="淘宝", order_no="R1", qty=1, status="signed",
                         paid_amount=Decimal("141"), sku_code="PPS0000000800000"))   # 无对应预估
    db_session.flush()
    svc.sync_fee_components(db_session)
    o = db_session.query(Order).filter_by(order_no="R1").first()
    assert o.est_packing == Decimal("14.10")    # 0.1 × 141
    assert o.est_logistics == Decimal("28.20")  # 0.2 × 141
