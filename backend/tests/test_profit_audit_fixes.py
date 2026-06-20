"""利润审计修复回归 (2026-06-20 31-agent审计后):
- platform_deduction 部分退款不再被当平台费双扣
- _pricing_cost_for factory_cost 回退补物流+安装(防双算护栏漏算)
- _multi_product_cost 有商品行未定价时整单回退(不把缺行的部分和当整单成本)
全部 sqlite 内存 + 合成。
"""
from datetime import date
from decimal import Decimal as D
from types import SimpleNamespace


def _db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    eng = create_engine("sqlite:///:memory:", future=True, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, autoflush=False, future=True)()


def test_platform_deduction_excludes_refund():
    # paid1000, recv930(=1000−20平台费−50退款), refund50 → 平台费应=20 而非70(退款不双扣)
    from app.services.order_financials import platform_deduction
    o = SimpleNamespace(paid_amount=D("1000"), shop_received_amount=D("930"),
                        refund_amount=D("50"), order_date=date(2026, 1, 1))
    coef = {"handling_rate": D("0.006"), "activity_rate": D("0.02"), "activity_since": date(2026, 5, 1)}
    assert platform_deduction(o, coef) == D("20")


def test_pricing_cost_factory_fallback_includes_logistics_install():
    from app.models.pricing import PricingSku
    from app.models.order import Order
    from app.services.order_cost_service import _pricing_cost_for
    db = _db()
    db.add(PricingSku(product_code="P1", sku="s", sku_code="S1",
                      physical_cost=None, factory_cost=D("1000"),
                      logistics_cost=D("200"), install_cost=D("100")))
    db.commit()
    o = Order(platform="淘宝", order_no="O1", product_code="P1", sku_code="S1")
    assert _pricing_cost_for(db, o) == D("1300")   # 出厂1000 + 物流200 + 安装100


def test_multi_product_returns_none_if_any_line_unpriced():
    from app.models.pricing import PricingSku
    from app.models.order import OrderDetail
    from app.services.order_cost_service import _multi_product_cost
    db = _db()
    db.add(PricingSku(product_code="P1", sku="s1", sku_code="S1", physical_cost=D("2080")))
    db.add(OrderDetail(sync_key="line:O1:0", order_no="O1", product_code="P1", sku_code="S1", qty=1, source="import"))
    db.add(OrderDetail(sync_key="line:O1:1", order_no="O1", product_code="P9", sku_code="S2", qty=1, source="import"))
    db.commit()
    # S2 无定价 → 不静默丢, 整单 None(回退, 不把单行2080当整单成本)
    assert _multi_product_cost(db, SimpleNamespace(order_no="O1")) is None
