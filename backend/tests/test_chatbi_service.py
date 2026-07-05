# -*- coding: utf-8 -*-
"""ChatBI 编排/路由 集成单测 (sqlite): 模板路由 + service 直算 + pointer + 离线降级 + 审计。

SQL 模板/半生成/直出走 PG 视图, sqlite 无视图 → 那几条路径在 D5 live 验; 这里覆盖:
  路由命中、service 模板真算(复用 order_financials/sales_analytics, sqlite 可跑)、
  pointer、AI 离线拒答、审计落库、反馈。
"""
from datetime import date

import pytest

from app.chatbi import llm_client
from app.chatbi import router as chatbi_router
from app.chatbi import service as chatbi_service
from app.models.chatbi_query import ChatbiQuery
from app.models.order import Order


@pytest.fixture()
def seeded(db_session):
    today = date.today()
    for i in range(3):
        db_session.add(Order(
            platform="淘宝", order_no=f"PS-T{i}", order_date=today, status="signed",
            paid_amount=1000 + i * 100, refund_amount=0, is_refill=False,
            product_code="PPS001", product_name="岩板餐边柜", qty=1,
        ))
    # 补单一条, 必须被经营口径排除
    db_session.add(Order(platform="淘宝", order_no="PS-R1", order_date=today, status="signed",
                         paid_amount=5000, is_refill=True, product_code="PPS001",
                         product_name="岩板餐边柜", qty=1))
    db_session.commit()
    return db_session


# ------------------------------- 路由 ------------------------------- #

def test_router_matches_net_profit():
    r = chatbi_router.route("本月净利润是多少", today=date(2026, 7, 5))
    assert r.kind == "template" and r.template.key == "monthly_net_profit"
    assert r.time_range and r.time_range.label == "本月"


def test_router_matches_margin_rank():
    r = chatbi_router.route("产品毛利率排行", today=date(2026, 7, 5))
    assert r.kind == "template" and r.template.key == "product_margin_rank"


def test_router_fallback_when_no_keyword():
    r = chatbi_router.route("帮我写一首诗", today=date(2026, 7, 5))
    assert r.kind == "fallback" and r.template is None


# ------------------------------- service 模板真算 ------------------------------- #

def test_net_profit_template_returns_numbers(seeded):
    resp = chatbi_service.ask(seeded, "本月净利润", username="tester")
    assert resp["route"] == "template" and resp["badge"] == "verified"
    assert resp["template_key"] == "monthly_net_profit"
    assert len(resp["rows"]) >= 4
    labels = {row[0] for row in resp["rows"]}
    assert "净利润" in labels and "营收(实付−退款)" in labels
    # 营收应排除补单(5000) → 只含 1000+1100+1200=3300
    rev = next(row[1] for row in resp["rows"] if row[0] == "营收(实付−退款)")
    assert abs(rev - 3300) < 0.01


def test_margin_rank_template(seeded):
    resp = chatbi_service.ask(seeded, "哪个产品利润率最高", username="tester")
    assert resp["route"] == "template" and resp["badge"] == "verified"
    assert resp["chart"]["type"] == "bar"


# ------------------------------- pointer ------------------------------- #

def test_pointer_template_ad_roi(db_session):
    resp = chatbi_service.ask(db_session, "广告真实ROI是多少", username="tester")
    assert resp["badge"] == "pointer"
    assert "广告数据尚未接入" in resp["message"]


# ------------------------------- 离线降级 ------------------------------- #

def test_fallback_refuses_when_llm_offline(db_session, monkeypatch):
    monkeypatch.setattr(llm_client, "is_available", lambda db: False)
    resp = chatbi_service.ask(db_session, "帮我分析一下随便什么", username="tester")
    assert resp["badge"] == "refused"
    assert "AI 引擎离线" in resp["message"]


# ------------------------------- 审计 + 反馈 ------------------------------- #

def test_audit_row_written_and_feedback(seeded):
    resp = chatbi_service.ask(seeded, "本月净利润", username="tester")
    qid = resp["query_id"]
    assert qid is not None
    row = seeded.get(ChatbiQuery, qid)
    assert row is not None and row.route == "template" and row.username == "tester"
    assert chatbi_service.set_feedback(seeded, qid, "up", "有用") is True
    assert seeded.get(ChatbiQuery, qid).feedback == "up"


def test_suggestions_present(db_session):
    resp = chatbi_service.ask(db_session, "广告真实ROI", username="t")
    assert isinstance(resp["suggestions"], list) and resp["suggestions"]
