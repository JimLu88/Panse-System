"""关闭单(status='cancelled')必须 100% 排除出一切财务统计 —— 4 处漏点回归测试。

用户铁律 2026-06-20: "状态'关闭'=正常退款、没有货品交易, 必须排除; 只有'交易成功'才算真实交易。"
背景: 生产库 455 个 cancelled 单, 373 个 paid_amount>0 (Σ¥1,828,555 老数据'paid=应付'脏值),
曾从 对账/工厂报表/补单成本/补单流水 4 条没走成交口径的路径漏进财务。
全部 sqlite 内存 + 合成 Order。
"""
from datetime import date
from decimal import Decimal as D


def _db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    eng = create_engine("sqlite:///:memory:", future=True, connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, autoflush=False, future=True)()


def _order(**kw):
    from app.models.order import Order
    d = dict(platform="淘宝", order_date=date(2026, 1, 15), is_refill=False, paid_amount=D("1000"),
             buyer_payable_amount=D("1000"), shop_received_amount=D("980"),
             refund_amount=D("0"), product_code="PPS1", status="signed")
    d.update(kw)
    return Order(**d)


def test_factory_statement_excludes_cancelled():
    # C: 工厂对账单 _excluded_reason 把关闭单挡在外面, 成交单保留
    from app.services.factory_statement_service import _excluded_reason
    assert _excluded_reason(_order(status="cancelled", paid_amount=D("9999"))) == "未成交(取消/待付款/全退)"
    assert _excluded_reason(_order(status="signed")) is None


def test_reconciliation_base_query_excludes_cancelled():
    # D: 逐笔对账 _base_query 排掉关闭单(¥183万假付款不进 paid_sum), 成交单保留
    from app.services.order_reconciliation_service import _base_query
    db = _db()
    db.add(_order(order_no="C1", status="cancelled", paid_amount=D("9999")))
    db.add(_order(order_no="S1", status="signed", paid_amount=D("1000")))
    db.commit()
    nos = {o.order_no for o in db.execute(_base_query()).scalars().all()}
    assert "C1" not in nos
    assert "S1" in nos


def test_reconciliation_base_query_keeps_null_status():
    # NULL-safe: 未知状态(NULL)不当成关闭, 仍保留供人工核对
    from app.services.order_reconciliation_service import _base_query
    db = _db()
    db.add(_order(order_no="N1", status=None, paid_amount=D("500")))
    db.commit()
    assert "N1" in {o.order_no for o in db.execute(_base_query()).scalars().all()}


def test_refill_cost_excludes_cancelled_refill():
    # A: 补单(刷单)成本只算成交的补单, 关闭的补单不计平台费/税/运费
    from app.services.order_financials import refill_cost
    db = _db()
    db.add(_order(order_no="RC", is_refill=True, status="cancelled", paid_amount=D("500"), actual_freight=D("20")))
    db.add(_order(order_no="RS", is_refill=True, status="signed", paid_amount=D("500"), actual_freight=D("20")))
    db.commit()
    coef = {"handling_rate": D("0.006"), "activity_rate": D("0.02"),
            "activity_since": date(2026, 5, 1), "tax_rate": D("0.02"),
            "fin_refill_commission_rate": D("0")}
    rc = refill_cost(db, date(2026, 1, 1), date(2026, 1, 31), coef)
    assert rc["count"] == 1  # 只剩 signed 的补单
    assert rc["freight"] == D("20.00")  # 只有成交补单的运费
