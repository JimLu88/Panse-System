"""月度财务报表 (优化 #10) 测试: 汇总数值 + Excel 生成。"""
from datetime import date
from decimal import Decimal

from app.models.order import Order
from app.services import financial_report_service


def test_monthly_summary_and_excel(db_session):
    db_session.add(Order(
        order_no="R1", platform="淘宝", status="paid", is_historical=False,
        order_date=date(2026, 5, 10), qty=1,
        paid_amount=Decimal("1000"), actual_cost=Decimal("600"),
    ))
    # 不同月份的订单不应计入 5 月
    db_session.add(Order(
        order_no="R2", platform="淘宝", status="paid", is_historical=False,
        order_date=date(2026, 4, 10), qty=1,
        paid_amount=Decimal("500"), actual_cost=Decimal("300"),
    ))
    db_session.commit()

    s = financial_report_service.monthly_summary(db_session, 2026, 5)
    assert s["period"] == "2026-05"
    assert s["order_count"] == 1
    assert s["revenue"] == 1000.0

    data = financial_report_service.build_excel(s)
    assert data[:2] == b"PK"          # xlsx 是 zip 容器
    assert len(data) > 100
