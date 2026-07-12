"""方案B价格版本化 (用户拍板 2026-07-12, 从今天起、不补历史):
① record_if_price_changed: 普通编辑/Excel导入没带生效日 → 跟踪字段将变时自动以【今天】为界封存旧值;
   无变化不封存; 同日重复改不重复封存。
② 订单成本读价点时点化: 修改日前订单按修改前价(wood_cost/external_parts_cost/physical), 修改日后按新价。
物料单价库(MaterialPriceHistory)早已同原则版本化, 不在此重复测。
"""
from datetime import date, timedelta
from decimal import Decimal

from app.models.order import Order
from app.models.pricing import PricingSku
from app.services import order_cost_service as oc
from app.services import pricing_version_service as pv


def _sku(db, sc="PPSV001011", wood=100, parts=30, physical=500):
    s = PricingSku(product_code="PPSV001", sku_code=sc, sku="测试床-1.5米", product_name="测试床",
                   daily_price=Decimal("1000"), wood_cost=Decimal(str(wood)),
                   external_parts_cost=Decimal(str(parts)), physical_cost=Decimal(str(physical)))
    db.add(s)
    db.flush()
    return s


def _order(db, no, day_offset=0, sku_code="PPSV001011"):
    o = Order(platform="淘宝", order_no=no, qty=1, sku_code=sku_code, product_code="PPSV001",
              product_name="测试床", sku="测试床-1.5米", status="paid", paid_amount=Decimal("900"),
              order_date=date.today() + timedelta(days=day_offset))
    db.add(o)
    db.flush()
    return o


def test_record_if_price_changed_semantics(db_session):
    db = db_session
    s = _sku(db)
    # 无变化 → 不封存
    assert pv.record_if_price_changed(db, s, {"wood_cost": Decimal("100")}) is False
    # 非跟踪字段 → 不封存
    assert pv.record_if_price_changed(db, s, {"remark": "x"}) is False
    # 真变化 → 封存(旧值100进版本)
    assert pv.record_if_price_changed(db, s, {"wood_cost": Decimal("120")}) is True
    s.wood_cost = Decimal("120")
    db.commit()
    # 同日再改 → 不重复封存
    assert pv.record_if_price_changed(db, s, {"wood_cost": Decimal("130")}) is False


def test_wood_and_parts_asof_old_order_old_price(db_session):
    db = db_session
    s = _sku(db, wood=100, parts=30)
    o_old = _order(db, "V-OLD", day_offset=-3)     # 改价前订单
    o_new = _order(db, "V-NEW", day_offset=0)      # 改价当天(=修改日后)订单
    # 今天改价: 先封存旧值(方案B自动版本化路径), 再写新值
    assert pv.record_if_price_changed(db, s, {"wood_cost": Decimal("150"),
                                              "external_parts_cost": Decimal("55")}) is True
    s.wood_cost, s.external_parts_cost = Decimal("150"), Decimal("55")
    db.commit()
    # 老单老价
    assert oc._pricing_wood_for(db, o_old) == Decimal("100")
    assert oc._pricing_parts_for(db, o_old) == Decimal("30")
    assert oc._wood_unit_price(db, s.sku_code, on_date=o_old.order_date) == Decimal("100")
    # 新单新价(改价当天起)
    assert oc._pricing_wood_for(db, o_new) == Decimal("150")
    assert oc._pricing_parts_for(db, o_new) == Decimal("55")
    assert oc._wood_unit_price(db, s.sku_code, on_date=o_new.order_date) == Decimal("150")


def test_no_version_falls_back_live(db_session):
    db = db_session
    s = _sku(db, sc="PPSV001012", wood=88)
    o = _order(db, "V-LIVE", day_offset=-30, sku_code="PPSV001012")
    # 从没改过价(无版本) → 老单也用 live(改造前行为, 不影响存量)
    assert oc._pricing_wood_for(db, o) == Decimal("88")
