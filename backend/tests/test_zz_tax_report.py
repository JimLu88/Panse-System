# -*- coding: utf-8 -*-
"""涉税报送税费真源 (用户拍板 2026-07-14): 已报送季度按报送净额×2%(打款口径),
未报送季度回退订单估算; Agent 未上线软失败不拖垮。"""
from datetime import date
from decimal import Decimal

from app.models.order import Order
from app.services import cash_flow_service as cfs
from app.services import tax_report_service as trs


def test_ingest_and_get(db_session):
    trs.ingest(db_session, {"2026-Q1": {"net_income": 491255.80, "gross": 495441.49, "refund": 4185.69}})
    got = trs.get_reported(db_session)
    assert Decimal(got["2026-Q1"]["net_income"]) == Decimal("491255.80")
    # 同季覆盖 + 异季保留
    trs.ingest(db_session, {"2026-Q2": {"net_income": 100000}, "2026-Q1": {"net_income": 491255.8}})
    got = trs.get_reported(db_session)
    assert set(got) == {"2026-Q1", "2026-Q2"}


def test_quarterly_tax_prefers_reported(db_session):
    """Q1 报送 491255.80 → 税 9825.12(非订单估算); Q3(当季)无报送 → 订单估算。"""
    db_session.add(Order(platform="淘宝", order_no="T1", status="signed", is_refill=False,
                         order_date=date(2026, 1, 15), paid_amount=Decimal("100000")))
    db_session.add(Order(platform="淘宝", order_no="T3", status="signed", is_refill=False,
                         order_date=date(2026, 7, 5), paid_amount=Decimal("50000")))
    db_session.commit()
    trs.ingest(db_session, {"2026-Q1": {"net_income": 491255.80}})
    r = cfs._quarterly_tax(db_session, today=date(2026, 7, 14))
    q1 = next(q for q in r["quarters"] if q["quarter"] == "2026-Q1")
    q3 = next(q for q in r["quarters"] if q["quarter"] == "2026-Q3")
    assert q1["tax"] == Decimal("9825.12") and q1["basis"] == "报送"
    assert q3["tax"] == Decimal("1000.00") and q3["basis"] == "估算"   # 50000×2%


def test_bad_reported_value_falls_back(db_session):
    """报送值坏(非数值) → 该季回退订单估算, 不炸。"""
    db_session.add(Order(platform="淘宝", order_no="T2", status="signed", is_refill=False,
                         order_date=date(2026, 4, 10), paid_amount=Decimal("10000")))
    db_session.commit()
    trs.ingest(db_session, {"2026-Q2": {"net_income": 123}})
    import json
    from app.services import settings_service
    settings_service.set_value(db_session, trs.SETTING_KEY,
                               json.dumps({"2026-Q2": {"net_income": "not-a-number"}}))
    r = cfs._quarterly_tax(db_session, today=date(2026, 7, 14))
    q2 = next(q for q in r["quarters"] if q["quarter"] == "2026-Q2")
    assert q2["tax"] == Decimal("200.00")   # 回退 10000×2%


def test_pull_soft_fails_without_agent(db_session, monkeypatch):
    """任务未上线/Agent 离线 → ok False, 不抛。"""
    from app.services import web_agent_service as wa
    monkeypatch.setattr(wa, "run_task", lambda db, tid, v=None: {"ok": False, "error": "offline"})
    r = trs.pull_via_agent(db_session)
    assert r["ok"] is False and r["stage"] == "run"
