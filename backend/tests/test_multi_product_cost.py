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


def _seed_amt(db, order_no, lines):
    """lines: [(sku_code, physical_cost, qty, amount)] — 带子行金额(用于退款行排除)。"""
    from app.models.order import OrderDetail
    from app.models.pricing import PricingSku
    for i, (sku, cost, qty, amt) in enumerate(lines):
        db.add(PricingSku(product_code=f"P{i}", sku=f"sku{i}", sku_code=sku, physical_cost=D(str(cost))))
        db.add(OrderDetail(sync_key=f"line:{order_no}:{i}", order_no=order_no,
                           sku_code=sku, qty=qty, amount=D(str(amt)), source="import"))
    db.commit()


def test_refunded_line_excluded():
    # 口径A: 留下餐桌¥2020(实付2636.73) + 退掉配置岩板(金额906.88, refund=906.88) → 只算餐桌
    db = _db()
    _seed_amt(db, "R1", [("S1", 2020, 1, 2636.73), ("S2", 770, 1, 906.88)])
    o = SimpleNamespace(order_no="R1", refund_amount=D("906.88"))
    assert _multi_product_cost(db, o) == D("2020")


def test_no_refund_sums_all_lines():
    # 两个都留(refund=0/None) → 全算: 餐桌2080 + 床头柜730×2 = 3540 (3307941 选b)
    db = _db()
    _seed_amt(db, "R2", [("S1", 2080, 1, 2705.19), ("S2", 730, 2, 1795.17)])
    assert _multi_product_cost(db, SimpleNamespace(order_no="R2", refund_amount=D("0"))) == D("3540")
    assert _multi_product_cost(db, SimpleNamespace(order_no="R2")) == D("3540")  # 无 refund_amount 属性


def test_cost_exceeds_paid_returns_none():
    # 护栏: 子行成本和 > 实付×1.1 → None (实付只覆盖部分子产品, 不硬套汇总造假亏)
    db = _db()
    _seed_amt(db, "G1", [("S1", 820, 1, 1054.90), ("S2", 770, 1, 989.67)])
    assert _multi_product_cost(db, SimpleNamespace(order_no="G1", paid_amount=D("989.67"))) is None
    # 实付够(两个都付) → 成本和1590 ≤ 2044×1.1 → 1590
    assert _multi_product_cost(db, SimpleNamespace(order_no="G1", paid_amount=D("2044.57"))) == D("1590")
