"""理论成本: 木作物料特殊处理 + 定价表回填."""
from decimal import Decimal

from app.models.bom import BomLine
from app.models.material import Material
from app.models.order import Order
from app.models.pricing import PricingSku
from app.services import order_cost_service


def _order(db, order_no="O1", sku_code="SKU1", product_code="P1", qty=1, status="signed"):
    o = Order(platform="淘宝", order_no=order_no, product_code=product_code,
              sku_code=sku_code, qty=qty, status=status)
    db.add(o)
    db.flush()
    return o


def _pricing(db, sku_code="SKU1", product_code="P1", accounting_cost=None, wood_cost=None,
             physical_cost=None):
    ps = PricingSku(product_code=product_code, sku_code=sku_code,
                    accounting_cost=accounting_cost, wood_cost=wood_cost,
                    physical_cost=physical_cost)
    db.add(ps)
    db.flush()
    return ps


# ----------------- 木作成本特殊处理 ----------------- #

def test_wood_material_priced_from_pricing_sku(db_session):
    """WD- 物料不在物料表, 单价取定价表 wood_cost."""
    db_session.add(Material(code="AC-0001", name="把手", price=Decimal("10")))
    db_session.add(BomLine(product_code="P1", sku_code="SKU1", material_code="AC-0001",
                           qty_per_product=Decimal("2")))
    db_session.add(BomLine(product_code="P1", sku_code="SKU1", material_code="WD-0001",
                           qty_per_product=Decimal("1")))
    db_session.flush()
    _pricing(db_session, wood_cost=Decimal("1360"))
    o = _order(db_session)

    bd = order_cost_service.compute(db_session, o)
    # 把手 2×10=20, 木作 1360 (不乘 qty_per) → 1380
    assert bd.unit_cost == Decimal("1380.00")
    assert bd.resolved
    assert bd.missing_price_count == 0


def test_wood_counted_once_across_multiple_wd_lines(db_session):
    """同 SKU 多条 WD 行 (木作拆件) 共用一个 wood_cost, 只计一次."""
    for code in ("WD-0001", "WD-0002", "WD-0003"):
        db_session.add(BomLine(product_code="P1", sku_code="SKU1", material_code=code,
                               qty_per_product=Decimal("1")))
    db_session.flush()
    _pricing(db_session, wood_cost=Decimal("1500"))
    o = _order(db_session)

    bd = order_cost_service.compute(db_session, o)
    # 三条 WD 行只计一次 1500, 其余两条计 0
    assert bd.unit_cost == Decimal("1500.00")
    wd_costs = [ln.line_cost for ln in bd.lines]
    assert Decimal("1500.00") in wd_costs
    assert wd_costs.count(Decimal("0.00")) == 2


def test_wood_missing_pricing_flags_note(db_session):
    """定价表无 wood_cost 时, 木作行标缺价, note 提示补木作成本."""
    db_session.add(BomLine(product_code="P1", sku_code="SKU1", material_code="WD-0001",
                           qty_per_product=Decimal("1")))
    db_session.flush()
    _pricing(db_session, wood_cost=None)
    o = _order(db_session)

    bd = order_cost_service.compute(db_session, o)
    assert bd.missing_price_count == 1
    assert "木作" in (bd.note or "")


# ----------------- 定价表回填理论成本 ----------------- #

def test_backfill_theoretical_from_pricing(db_session):
    # 口径(2026-06-18): 理论成本=物理总成本(商品+物流+安装), 税/扣点由会计汇总按实付另算
    _pricing(db_session, sku_code="SKU1", physical_cost=Decimal("2811.21"),
             accounting_cost=Decimal("3100"))
    o = _order(db_session)
    assert o.theoretical_cost is None

    res = order_cost_service.backfill_theoretical_from_pricing(db_session)
    assert res["updated"] == 1
    db_session.refresh(o)
    assert o.theoretical_cost == Decimal("2811.21")


def test_backfill_skips_cancelled(db_session):
    """交易关闭 (cancelled) 订单跳过, 不回填."""
    _pricing(db_session, sku_code="SKU1", accounting_cost=Decimal("1000"))
    o = _order(db_session, status="cancelled")

    res = order_cost_service.backfill_theoretical_from_pricing(db_session, skip_closed=True)
    assert res["skipped_closed"] == 1
    assert res["updated"] == 0
    db_session.refresh(o)
    assert o.theoretical_cost is None


def test_backfill_falls_back_to_product_code(db_session):
    """sku_code 在定价表查不到时, 退到 product_code 任一有成本的定价行."""
    _pricing(db_session, sku_code="SKU_OTHER", product_code="P1", physical_cost=Decimal("888"),
             accounting_cost=Decimal("1000"))
    o = _order(db_session, sku_code="SKU_NOPRICE", product_code="P1")

    res = order_cost_service.backfill_theoretical_from_pricing(db_session)
    assert res["updated"] == 1
    db_session.refresh(o)
    assert o.theoretical_cost == Decimal("888.00")


def test_backfill_no_pricing_counted(db_session):
    o = _order(db_session, sku_code="SKU_NONE", product_code="P_NONE")
    res = order_cost_service.backfill_theoretical_from_pricing(db_session)
    assert res["skipped_no_pricing"] == 1
    assert res["updated"] == 0
