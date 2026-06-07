# -*- coding: utf-8 -*-
"""定制订单缺定制需求 → 异常分类扫描测试。"""
from decimal import Decimal

from app.models.exception import DataException
from app.models.order import Order
from app.services import custom_order_spec_service as C


def _add(db, order_no, *, sku=None, name=None, remark=None, is_custom=False, status="paid"):
    db.add(Order(platform="淘宝", order_no=order_no, status=status,
                 sku=sku, product_name=name, remark=remark, is_custom=is_custom,
                 paid_amount=Decimal("1000")))


def test_scan_flags_custom_without_spec(db_session):
    db = db_session
    _add(db, "C1", sku="其他尺寸定制", name="畔色实木餐边柜")        # 定制无尺寸 → 标记
    _add(db, "C2", sku="定制餐桌", remark="长1800宽900高750")       # 定制有尺寸 → 不标
    _add(db, "N1", sku="榉木床头柜-标准[长45cm]")                   # 非定制 → 忽略
    _add(db, "C3", name="餐桌", is_custom=True, remark="客户要红色")  # 定制无尺寸(备注无规格) → 标记
    db.flush()

    r = C.scan(db)
    assert r["custom_orders"] == 3        # C1/C2/C3
    assert r["missing_spec"] == 2         # C1/C3
    assert r["created"] == 2
    excs = db.query(DataException).filter_by(exception_type="custom_order_missing_spec").all()
    assert {e.source_pk for e in excs} == {"C1", "C3"}


def test_scan_dedup_and_autoresolve(db_session):
    db = db_session
    _add(db, "C1", sku="其他尺寸定制")
    db.flush()
    assert C.scan(db)["created"] == 1
    # 再扫 → 不重复建
    assert C.scan(db)["created"] == 0
    # 补上定制需求 → 自动解决
    db.query(Order).filter_by(order_no="C1").one().remark = "长1600×宽800"
    db.flush()
    r = C.scan(db)
    assert r["resolved"] == 1
    e = db.query(DataException).filter_by(source_pk="C1").one()
    assert e.status == "resolved"
