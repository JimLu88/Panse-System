"""NPD P3: 上市后复盘 — 按 product_code 拉销量/退货/成本, 给反哺结论。"""
from __future__ import annotations

from decimal import Decimal

from app.models.order import Order
from app.services import npd_service


def test_review_unavailable_without_product_code(db_session):
    npd_service.seed_stages(db_session)
    p = npd_service.create_project(db_session, name="未落地")
    r = npd_service.review_project(db_session, p)
    assert r["available"] is False


def test_review_pulls_sales_margin_and_recs(db_session):
    npd_service.seed_stages(db_session)
    p = npd_service.create_project(db_session, name="复盘单",
                                   target_price=Decimal("1000"), target_margin_rate=Decimal("0.30"))
    p.product_code = "PBS26010010001"
    db_session.commit()
    npd_service.save_cost_gate(db_session, p, est_mass_cost=Decimal("600"))
    mk = lambda no, **kw: Order(platform="淘宝", order_no=no, product_code="PBS26010010001",
                                qty=1, status="signed", **kw)
    db_session.add(mk("O1", paid_amount=Decimal("1000"), actual_cost=Decimal("600")))
    db_session.add(mk("O2", paid_amount=Decimal("1000"), actual_cost=Decimal("600")))
    db_session.add(mk("O3", paid_amount=Decimal("1000"), refund_amount=Decimal("1000")))
    db_session.add(mk("O4", paid_amount=Decimal("1000"), is_refill=True))   # 刷单, 排除
    db_session.commit()

    r = npd_service.review_project(db_session, p)
    assert r["available"] is True
    assert r["orders"] == 3                        # O1/O2/O3, 排除刷单 O4
    assert Decimal(r["revenue"]) == Decimal("3000")
    assert Decimal(r["refunds"]) == Decimal("1000")
    assert r["recommendations"]                    # 有反哺结论(毛利不达标/退货高)
