"""价格/成本变更留痕 (优化 #5) 测试。"""
from decimal import Decimal

from sqlalchemy import select

from app.api.pricing import _record_price_changes
from app.models.price_change import PriceChangeLog
from app.models.pricing import PricingSku


def test_price_change_recorded(db_session):
    sku = PricingSku(sku_code="SK1", product_code="P1", list_price=Decimal("100"))
    db_session.add(sku)
    db_session.commit()

    # 改 list_price (跟踪) + remark (不跟踪)
    _record_price_changes(db_session, sku, {"list_price": Decimal("120"), "remark": "x"}, actor="alice")
    db_session.commit()

    logs = db_session.execute(
        select(PriceChangeLog).where(PriceChangeLog.sku_code == "SK1")
    ).scalars().all()
    assert len(logs) == 1                      # 只记跟踪字段
    assert logs[0].field == "list_price"
    # old 取自已存的 Numeric(2位) → "100.00"; new 取自原始输入 → "120"
    assert logs[0].old_value == "100.00" and logs[0].new_value == "120"
    assert logs[0].actor == "alice"


def test_no_change_no_log(db_session):
    sku = PricingSku(sku_code="SK2", product_code="P2", list_price=Decimal("50"))
    db_session.add(sku)
    db_session.commit()
    _record_price_changes(db_session, sku, {"list_price": Decimal("50")}, actor="bob")  # 没变
    db_session.commit()
    logs = db_session.execute(
        select(PriceChangeLog).where(PriceChangeLog.sku_code == "SK2")
    ).scalars().all()
    assert logs == []
