# -*- coding: utf-8 -*-
"""第二阶段: 定制单套常规同尺寸款 regular_size_cost (2026-07-02, 用户拍板 4 方向 + 护栏)。
常规款梯度(榉木岩板餐桌): 120→1980 140→2020 160→2080 180→2150 200→2430。
护栏: 多商品/片段(实付<常规×70%)/超大 不套; 无尺寸完整单标 missing_size; 洞石+300。"""
from decimal import Decimal

from app.models.order import Order
from app.models.pricing import PricingSku
from app.services import order_cost_service as ocs
from app.services import order_financials as ofin
from app.services import custom_order_reconcile_service as cust


def _seed(db):
    rows = [("PPS2421007090111", "榉木餐桌-1.2米-白色岩板", 1980),
            ("PPS2421007090112", "榉木餐桌-1.4米-白色岩板", 2020),
            ("PPS2421007090113", "榉木餐桌-1.6米-白色岩板", 2080),
            ("PPS2421007090114", "榉木餐桌-1.8米-白色岩板", 2150),
            ("PPS2421007090115", "榉木餐桌-2.0米-白色岩板", 2430),
            ("PPS2421007090199", "其他尺寸定制咨询", None)]
    for code, name, phys in rows:
        db.add(PricingSku(sku_code=code, product_code="PPS24210070901", sku=name,
                          physical_cost=(Decimal(str(phys)) if phys is not None else None)))
    db.flush()


def _o(**kw):
    base = dict(order_no="X", is_custom=True, product_code="PPS24210070901",
               sku_code="PPS2421007090199", sku="其他尺寸定制咨询")
    base.update(kw)
    return Order(**base)


def test_standard_size(db_session):
    """标准1.8米 完整单 → 套 2150。"""
    _seed(db_session)
    c, k = ocs.regular_size_cost(db_session, _o(paid_amount=Decimal("3409"), remark="尺寸1800×750mm"))
    assert c == Decimal("2150.00") and k == "regular"


def test_nonstandard_upsize(db_session):
    """非标 1.35米(135cm) → 向上取 1.4米(140) = 2020。"""
    _seed(db_session)
    c, k = ocs.regular_size_cost(db_session, _o(paid_amount=Decimal("2739"), remark="定制1.35*0.6"))
    assert c == Decimal("2020.00") and k == "regular_upsize"


def test_nonstandard_upsize_110(db_session):
    """非标 110cm → 向上取 120(1.2米) = 1980。"""
    _seed(db_session)
    c, k = ocs.regular_size_cost(db_session, _o(paid_amount=Decimal("2500"), remark="定制110*70"))
    assert c == Decimal("1980.00") and k == "regular_upsize"


def test_dongshi_surcharge(db_session):
    """洞石 1.8米 → 常规2150 + 300 = 2450。"""
    _seed(db_session)
    c, k = ocs.regular_size_cost(db_session, _o(paid_amount=Decimal("3600"), remark="定制1800×750 洞石岩板"))
    assert c == Decimal("2450.00") and k == "regular_dongshi"


def test_multi_product_not_matched(db_session):
    """多商品单 → 不套(multi护栏)。"""
    _seed(db_session)
    c, k = ocs.regular_size_cost(db_session, _o(paid_amount=Decimal("5355"), remark="本单含2个商品 1800×750"))
    assert c is None and k == "multi"


def test_fragment_by_ratio(db_session):
    """实付¥200 远小于 常规2150×70% → 片段, 不套(挡住误匹配)。"""
    _seed(db_session)
    c, k = ocs.regular_size_cost(db_session, _o(paid_amount=Decimal("200"), remark="1.8米改洞石岩板"))
    assert c is None and k == "fragment"


def test_missing_size_full_order(db_session):
    """完整单(实付达标)但无尺寸 → missing_size(提示人工补), 不套。"""
    _seed(db_session)
    c, k = ocs.regular_size_cost(db_session, _o(paid_amount=Decimal("2722"), remark=""))
    assert c is None and k == "missing_size"


def test_no_size_small_is_fragment(db_session):
    """无尺寸的小额单 → fragment(非缺尺寸)。"""
    _seed(db_session)
    c, k = ocs.regular_size_cost(db_session, _o(paid_amount=Decimal("30"), remark=""))
    assert c is None and k == "fragment"


def test_oversize(db_session):
    """定制 2.4米 > 最大常规2.0米 → oversize, 不套。"""
    _seed(db_session)
    c, k = ocs.regular_size_cost(db_session, _o(paid_amount=Decimal("4000"), remark="定制2400×900"))
    assert c is None and k == "oversize"


def test_non_custom(db_session):
    """非定制单 → not_custom。"""
    _seed(db_session)
    o = _o(is_custom=False, sku_code="PPS2421007090114", sku="榉木餐桌-1.8米-白色岩板",
           paid_amount=Decimal("3000"), remark="1800")
    c, k = ocs.regular_size_cost(db_session, o)
    assert c is None and k == "not_custom"


def test_not_applicable_product(db_session):
    """非白名单产品(餐边柜等,非岩板餐桌) → not_applicable, 不套(用户 2026-07-02: 仅限岩板餐桌)。"""
    _seed(db_session)
    o = _o(product_code="PPS23250050202", sku_code="PPS2325005020299",
           paid_amount=Decimal("9778"), remark="定制1600×2105尺寸")
    c, k = ocs.regular_size_cost(db_session, o)
    assert c is None and k == "not_applicable"


# ── 第二阶段接入: physical_cost_breakdown / 两表 传 db → 套常规款 ──
def test_breakdown_db_uses_regular(db_session):
    """逐单/定制核对(传 db): 定制1.8米 → 套常规款 2150(不 floor 不叠打包); 无 db → 旧口径(floor)。"""
    _seed(db_session)
    o = _o(paid_amount=Decimal("3661"), theoretical_cost=Decimal("2150"),
           est_packing=Decimal("170"), remark="定制远期单 尺寸1800×750mm 榉木岩板餐桌")
    bd_new = ofin.physical_cost_breakdown(o, db_session)
    bd_old = ofin.physical_cost_breakdown(o)
    assert bd_new["cap_mode"] == "套常规同尺寸款"
    assert bd_new["final"] == Decimal("2150.00")
    assert bd_old["cap_mode"] != "套常规同尺寸款"      # 无 db → 不套(其余调用点不变)


def test_breakdown_db_multi_guardrail(db_session):
    """主口径也认多商品护栏: 传 db 但"本单含2个商品" → 不套, 走原口径。"""
    _seed(db_session)
    o = _o(paid_amount=Decimal("5355"), theoretical_cost=Decimal("2000"),
           est_packing=Decimal("170"), remark="本单含2个商品 1800×750")
    bd = ofin.physical_cost_breakdown(o, db_session)
    assert bd["cap_mode"] != "套常规同尺寸款"


def test_both_tables_equal_with_db(db_session):
    """两表恒等: 定制单核对表 _row(传db) == 逐单核对表 physical_cost_breakdown(o, db) = 2150。"""
    _seed(db_session)
    o = _o(paid_amount=Decimal("3661"), theoretical_cost=Decimal("2150"),
           est_packing=Decimal("170"), remark="定制尺寸1800×750mm")
    row = cust._row(db_session, o, {"source": "regular"})   # _row 内部走 physical_cost_breakdown(o, db)
    per = float(ofin.physical_cost_breakdown(o, db_session)["final"])
    assert row["projected_cost"] == per == 2150.0
