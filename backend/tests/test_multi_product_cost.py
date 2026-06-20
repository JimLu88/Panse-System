"""一单多宝贝 成本按行汇总(杜绝塌单漏算)测试。用户拍板 2026-06-20 C项。

order_details(source='import')各商品行 pricing物理成本×qty 汇总; ≥2行才算多产品。
全部 sqlite 内存 + 合成数据。
"""
from decimal import Decimal as D
from types import SimpleNamespace

from app.services.order_cost_service import _multi_product_cost


def _db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    eng = create_engine("sqlite:///:memory:", future=True, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, autoflush=False, future=True)()


def _seed(db, order_no, lines):
    """lines: [(sku_code, physical_cost, qty)]"""
    from app.models.order import OrderDetail
    from app.models.pricing import PricingSku
    for i, (sku, cost, qty) in enumerate(lines):
        db.add(PricingSku(product_code=f"P{i}", sku=f"sku{i}", sku_code=sku, physical_cost=D(str(cost))))
        db.add(OrderDetail(sync_key=f"line:{order_no}:{i}", order_no=order_no,
                           sku_code=sku, qty=qty, source="import"))
    db.commit()


def test_multi_product_sums_lines():
    # 餐桌¥2080 + 床¥3160 = ¥5240 (塌单前只算了餐桌)
    db = _db()
    _seed(db, "O1", [("S1", 2080, 1), ("S2", 3160, 1)])
    assert _multi_product_cost(db, SimpleNamespace(order_no="O1")) == D("5240")


def test_qty_multiplied_per_line():
    # 2个床头窄柜¥730 + 1餐桌¥2080 = 730×2+2080 = ¥3540
    db = _db()
    _seed(db, "O3", [("S1", 730, 2), ("S2", 2080, 1)])
    assert _multi_product_cost(db, SimpleNamespace(order_no="O3")) == D("3540")


def test_single_line_returns_none():
    # 单产品(<2行) → None, 走原单SKU路径
    db = _db()
    _seed(db, "O2", [("S1", 2080, 1)])
    assert _multi_product_cost(db, SimpleNamespace(order_no="O2")) is None


def test_no_import_lines_returns_none():
    db = _db()
    assert _multi_product_cost(db, SimpleNamespace(order_no="NONE")) is None
