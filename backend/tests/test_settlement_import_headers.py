"""聚合结算新旧表头均须保留母订单关联，缺表头不能写孤立流水。"""
from io import BytesIO
from decimal import Decimal

import openpyxl
from sqlalchemy import select

from app.models.settlement import OrderSettlement
from app.services.settlement_import_service import import_bill


def _bill(order_header: str | None) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["入账时间", "支付流水号", order_header or "无关列", "入账类型", "收入金额（元）", "支出金额（元）"])
    ws.append(["2026-08-22 10:51:44", "2027833352102349825", "5125529739426102533", "交易收款", 3965, 0])
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_old_and_new_order_headers_are_linked_and_idempotent(db_session):
    for header in ("主订单id", "淘宝订单编号"):
        result = import_bill(db_session, _bill(header), source="agent")
        assert result["inserted"] + result["updated"] == 1
        rec = db_session.execute(select(OrderSettlement)).scalar_one()
        assert rec.order_no == "5125529739426102533"
        assert rec.source == "agent"
        assert rec.income == Decimal("3965")
    assert db_session.execute(select(OrderSettlement)).scalars().all().__len__() == 1


def test_missing_order_header_fails_without_writes(db_session):
    result = import_bill(db_session, _bill(None), source="agent")
    assert result["inserted"] == 0
    assert "error" in result
    assert db_session.execute(select(OrderSettlement)).scalars().all() == []


def test_agent_ingest_does_not_mark_invalid_bill_imported(db_session):
    from pathlib import Path
    from app.services.agent_ingest_service import _import_one
    kind, status, report = _import_one(db_session, 'settlement', Path('bill.xlsx'), _bill(None))
    assert kind == 'settlement'
    assert status == 'error'
    assert report['error']
    assert db_session.execute(select(OrderSettlement)).scalars().all() == []
