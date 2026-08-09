import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.main import app
from app.models import Base
from app.models.procurement import ProcurementInquiry
from app.services import procurement_service


def _seed_agent_task(db_session):
    task = procurement_service.create_task(
        db_session,
        {
            "title": "淘宝采购代理联调",
            "category": "daily",
            "item_name": "M6 螺丝",
            "quantity": 100,
            "unit": "颗",
            "execution_mode": "agent",
            "taobao_client_mode": "desktop",
            "channels": ["taobao"],
            "planned_merchant_count": 2,
            "max_followup_rounds": 2,
            "ab_test_enabled": True,
            "ab_test_sample_size": 2,
        },
        created_by="test",
    )
    procurement_service.review_scripts(
        db_session,
        task,
        script_a="您好，请提供 M6 螺丝含税含运报价、起订量和交期。",
        script_b="您好，我们在筛选五金长期供应商，请列出材质、交期和阶梯价。",
        reviewed_by="test",
    )
    inquiries = procurement_service.prepare_inquiries(
        db_session,
        task,
        [{"merchant_name": "五金商家一"}, {"merchant_name": "五金商家二"}],
    )
    procurement_service.review_inquiry_message(
        db_session,
        task,
        inquiries[0],
        content=procurement_service.initial_message(task, inquiries[0]),
        reviewed_by="test",
    )
    db_session.commit()
    return task


def _seed_discovery_task(db_session):
    task = procurement_service.create_task(
        db_session,
        {
            "title": "拼多多候选发现联调",
            "category": "production",
            "item_name": "电力轨道",
            "specification": "黑色 1 米",
            "quantity": 20,
            "unit": "条",
            "execution_mode": "agent",
            "channels": ["pinduoduo"],
            "planned_merchant_count": 1,
            "max_followup_rounds": 2,
            "ab_test_enabled": False,
            "ab_test_sample_size": 0,
        },
        created_by="test",
    )
    procurement_service.review_scripts(
        db_session,
        task,
        script_a="您好，请报电力轨道含税含运价格和交期。",
        script_b=None,
        reviewed_by="test",
    )
    inquiry = procurement_service.prepare_inquiries(db_session, task)[0]
    db_session.commit()
    return task, inquiry


@pytest.fixture()
def api_db():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_machine_api_claim_send_and_reply_roundtrip(api_db, monkeypatch):
    monkeypatch.setenv("PROCUREMENT_AGENT_TOKEN", "agent-test-token")
    task = _seed_agent_task(api_db)

    def override_db():
        yield api_db

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    headers = {"X-API-Key": "agent-test-token"}
    try:
        heartbeat = client.post(
            "/api/procurement/agent/heartbeat",
            headers=headers,
            json={
                "agent_id": "purchase-pc-test",
                "display_name": "采购测试机",
                "mode": "review",
                "capabilities": ["taobao_desktop"],
            },
        )
        assert heartbeat.status_code == 200

        claimed = client.post(
            "/api/procurement/agent/claim",
            headers=headers,
            json={
                "agent_id": "purchase-pc-test",
                "mode": "review",
                "capabilities": ["taobao_desktop"],
                "max_actions": 1,
            },
        )
        assert claimed.status_code == 200
        action = claimed.json()["actions"][0]
        assert action["task_id"] == task.id
        assert action["required_capability"] == "taobao_desktop"
        assert action["lease_token"]

        sent_payload = {
            "agent_id": "purchase-pc-test",
            "lease_token": action["lease_token"],
            "content": action["suggested_message"],
            "external_message_id": "api-out-001",
            "external_thread_id": "api-thread-001",
        }
        sent = client.post(
            f"/api/procurement/agent/inquiries/{action['inquiry_id']}/sent",
            headers=headers,
            json=sent_payload,
        )
        assert sent.status_code == 200
        assert sent.json()["duplicate"] is False

        duplicate = client.post(
            f"/api/procurement/agent/inquiries/{action['inquiry_id']}/sent",
            headers=headers,
            json={**sent_payload, "lease_token": "already-cleared"},
        )
        assert duplicate.status_code == 200
        assert duplicate.json()["duplicate"] is True

        reply = client.post(
            f"/api/procurement/agent/inquiries/{action['inquiry_id']}/reply",
            headers=headers,
            json={
                "agent_id": "purchase-pc-test",
                "content": "可以，含运单价 0.18 元",
                "external_message_id": "api-in-001",
                "quote_complete": True,
                "normalized_unit_price": 0.18,
                "response_quality": 90,
            },
        )
        assert reply.status_code == 200
        assert reply.json()["inquiry_status"] == "completed"
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_machine_api_rejects_wrong_token(api_db, monkeypatch):
    monkeypatch.setenv("PROCUREMENT_AGENT_TOKEN", "agent-test-token")

    def override_db():
        yield api_db

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    try:
        response = client.post(
            "/api/procurement/agent/claim",
            headers={"X-API-Key": "wrong-token"},
            json={
                "agent_id": "bad-agent",
                "mode": "dry_run",
                "capabilities": ["taobao_desktop"],
            },
        )
        assert response.status_code == 401
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_machine_api_discovery_requires_candidate_then_message_review(api_db, monkeypatch):
    monkeypatch.setenv("PROCUREMENT_AGENT_TOKEN", "agent-test-token")
    task, inquiry = _seed_discovery_task(api_db)

    def override_db():
        yield api_db

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)
    headers = {"X-API-Key": "agent-test-token"}
    claim_body = {
        "agent_id": "purchase-pc-test",
        "mode": "review",
        "capabilities": ["pinduoduo_chrome"],
        "max_actions": 1,
    }
    try:
        discovery = client.post(
            "/api/procurement/agent/discovery/claim",
            headers=headers,
            json=claim_body,
        )
        assert discovery.status_code == 200
        action = discovery.json()["actions"][0]
        candidate = client.post(
            f"/api/procurement/agent/inquiries/{inquiry.id}/candidate",
            headers=headers,
            json={
                "agent_id": "purchase-pc-test",
                "lease_token": action["lease_token"],
                "merchant_name": "轨道源头店",
                "merchant_external_id": "pdd-mall-1",
                "product_url": "https://mobile.yangkeduo.com/goods.html?goods_id=1",
                "discovery_query": action["search_query"],
                "candidate_score": 90,
                "candidate_snapshot": {"title": "电力轨道", "cookie": "blocked"},
            },
        )
        assert candidate.status_code == 200
        api_db.expire_all()
        stored = api_db.get(ProcurementInquiry, inquiry.id)
        assert stored.status == "ready"
        assert stored.candidate_snapshot == {"title": "电力轨道"}

        blocked = client.post(
            "/api/procurement/agent/claim", headers=headers, json=claim_body
        )
        assert blocked.json()["actions"] == []

        procurement_service.review_inquiry_message(
            api_db,
            task,
            stored,
            content=procurement_service.initial_message(task, stored),
            reviewed_by="test",
        )
        api_db.commit()
        allowed = client.post(
            "/api/procurement/agent/claim", headers=headers, json=claim_body
        )
        assert allowed.json()["actions"][0]["inquiry_id"] == inquiry.id
    finally:
        app.dependency_overrides.pop(get_db, None)
