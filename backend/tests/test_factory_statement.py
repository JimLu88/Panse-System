"""工厂对账单生成服务测试 (factory_statement_service)。

锁口径: 盈亏平衡 = 售价 − (accounting−factory)×qty; 安全垫 = 售价 − accounting×qty。
全部 sqlite 内存 + 合成数据。
"""
from datetime import date
from decimal import Decimal as D

from app.services import factory_statement_service as fss


def _db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    eng = create_engine("sqlite:///:memory:", future=True, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, autoflush=False, future=True)()


def test_generate_break_even():
    from app.models.order import Order
    from app.models.pricing import PricingSku
    db = _db()
    db.add(PricingSku(product_code="P1", sku="餐桌-1.4米", sku_code="P1-1.4",
                      factory_cost=D("1000"), accounting_cost=D("1400")))
    db.add(Order(platform="淘宝", order_no="O1", product_code="P1", product_name="餐桌",
                 sku="餐桌-1.4米", sku_code="P1-1.4", qty=1, order_date=date(2026, 6, 1),
                 status="signed", paid_amount=D("3000"), shop_received_amount=D("3000")))
    db.commit()
    r = fss.generate(db, period="2026-06")
    assert r["count"] == 1
    row = r["rows"][0]
    assert row["factory_predicted"] == 1000.0
    assert row["break_even_factory"] == 2600.0      # 3000 − (1400−1000)
    assert row["break_even_buffer"] == 1600.0        # 2600 − 1000 = 3000 − 1400
    assert r["totals"]["break_even_factory"] == 2600.0
    assert r["missing"] == 0


def test_excludes_refill_and_no_product():
    from app.models.order import Order
    db = _db()
    db.add(Order(platform="淘宝", order_no="O2", product_code="P1", qty=1,
                 is_refill=True, order_date=date(2026, 6, 1)))
    db.add(Order(platform="淘宝", order_no="O3", product_code=None, qty=1,
                 order_date=date(2026, 6, 1)))
    db.commit()
    assert fss.generate(db, period="2026-06")["count"] == 0   # 补单 + 无产品 都排除


def test_period_filter_and_available():
    from app.models.order import Order
    db = _db()
    db.add(Order(platform="淘宝", order_no="O4", product_code="P1", qty=1, order_date=date(2026, 5, 1),
                 status="signed", paid_amount=D("100")))
    db.add(Order(platform="淘宝", order_no="O5", product_code="P1", qty=1, order_date=date(2026, 6, 1),
                 status="signed", paid_amount=D("100")))
    db.commit()
    assert fss.available_periods(db) == ["2026-06", "2026-05"]
    assert fss.generate(db, period="2026-05")["count"] == 1


def test_excludes_sample_sale_order():
    """样品出货单(有关联样品)不进工厂对账单。"""
    from app.models.order import Order
    from app.models.marketing import Sample
    db = _db()
    db.add(Order(platform="淘宝", order_no="S1", product_code="P1", qty=1,
                 order_date=date(2026, 6, 1), status="signed", paid_amount=D("2000"),
                 shop_received_amount=D("2000")))
    db.add(Sample(sample_no="SP1", product_code="P1", status="已售", related_order_no="S1"))
    db.commit()
    assert fss.generate(db, period="2026-06")["count"] == 0


def test_missing_factory_cost_flagged():
    from app.models.order import Order
    db = _db()
    db.add(Order(platform="淘宝", order_no="O6", product_code="P9", product_name="无价品",
                 sku="未知", qty=1, order_date=date(2026, 6, 1),
                 status="signed", paid_amount=D("500"), shop_received_amount=D("500")))
    db.commit()
    r = fss.generate(db, period="2026-06")
    row = r["rows"][0]
    assert row["factory_predicted"] is None and row["break_even_factory"] is None
    assert "缺工厂价" in row["note"] and r["missing"] == 1
