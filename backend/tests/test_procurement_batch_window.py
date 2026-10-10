from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
from types import SimpleNamespace

from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.procurement import TaskCreate, TaskPatch, router
from app.database import get_db
from app.dependencies import get_current_user
from app.models import Base
from app.models.procurement import ProcurementInquiry, ProcurementMessage, ProcurementTask
from app.services import procurement_batch_service as batch
from app.services import procurement_service as service

START = datetime(2026, 9, 26, 2, 0, tzinfo=timezone.utc)


def seeded(db, *, count=2, policy=True, mode="assisted"):
    task = service.create_task(db, {
        "title": "离线批次", "item_name": "合成配件", "specification": "测试规格",
        "requirements": "测试资料，不发送", "quantity": 2, "unit": "件",
        "channels": ["taobao"], "planned_merchant_count": count,
        "execution_mode": mode, "ab_test_enabled": False, "max_followup_rounds": 1,
        "batch_policy_version": batch.POLICY_VERSION if policy else None,
    }, created_by="test")
    service.review_scripts(db, task, script_a="离线测试内容", script_b=None, reviewed_by="test")
    rows = service.prepare_inquiries(db, task, [
        {"merchant_name": f"合成商家{i}", "merchant_external_id": f"shop-{i}"} for i in range(count)
    ])
    db.commit()
    return task, rows


@pytest.mark.parametrize("count", [1, 10, 20, 30, 50])
def test_selectable_count_and_queue_match(db_session, count):
    task, rows = seeded(db_session, count=count)
    assert len(rows) == count
    assert len({row.slot_no for row in rows}) == count
    assert not task.ab_test_enabled and task.ab_test_sample_size == 0
    assert not task.started_at and not task.deadline_at
    assert service.due_actions(db_session, task_id=task.id, now=START) == []


@pytest.mark.parametrize("count", [0, 51, -1, True, 1.5, "50", None])
def test_count_strict_in_api_and_service(db_session, count):
    payload = {"title": "fixture", "item_name": "fixture", "planned_merchant_count": count}
    with pytest.raises(ValidationError):
        TaskCreate(**payload)
    with pytest.raises(ValueError):
        service.create_task(db_session, payload, created_by="test")


def test_new_api_defaults_do_not_enable_ab():
    payload = TaskCreate(title="fixture", item_name="fixture", planned_merchant_count=1)
    assert payload.ab_test_enabled is False
    assert payload.batch_policy_version == batch.POLICY_VERSION
    with pytest.raises(ValidationError):
        TaskPatch(planned_merchant_count=True)


def test_explicit_activation_is_idempotent_and_frozen(db_session):
    task, _ = seeded(db_session)
    batch.activate(db_session, task, activated_by="test", now=START)
    db_session.commit()
    snapshot = deepcopy(task.policy_snapshot)
    assert batch.utc(task.started_at) == START
    assert batch.utc(task.deadline_at) == START + timedelta(hours=48)
    assert snapshot["orders_allowed"] is False and snapshot["agent_execution_ready"] is False
    batch.activate(db_session, task, activated_by="other", now=START + timedelta(hours=4))
    assert task.policy_snapshot == snapshot
    assert batch.utc(task.started_at) == START
    with pytest.raises(ValueError):
        service.review_scripts(db_session, task, script_a="改稿", script_b=None, reviewed_by="test")


def test_missing_requirements_prevent_activation_without_starting_clock(db_session):
    task, _ = seeded(db_session)
    task.specification = None
    db_session.commit()
    with pytest.raises(ValueError, match="补齐"):
        batch.activate(db_session, task, activated_by="test", now=START)
    assert task.started_at is None


def test_whitespace_requirements_cannot_start_clock(db_session):
    task, _ = seeded(db_session)
    task.requirements = "  "
    db_session.commit()
    with pytest.raises(ValueError, match="补齐"):
        batch.activate(db_session, task, activated_by="test", now=START)
    assert task.started_at is None


@pytest.mark.parametrize("offset,open_expected", [(0, True), (172799, True), (172800, False), (172801, False)])
def test_exact_deadline_boundary(db_session, offset, open_expected):
    task, _ = seeded(db_session)
    batch.activate(db_session, task, activated_by="test", now=START)
    assert batch.window_open(task, now=START + timedelta(seconds=offset)) is open_expected
    assert bool(service.due_actions(db_session, task_id=task.id, now=START + timedelta(seconds=offset))) is open_expected


def test_all_legacy_agent_paths_block_new_batch_even_when_active(db_session, monkeypatch):
    task, rows = seeded(db_session, mode="agent")
    batch.activate(db_session, task, activated_by="test", now=START)
    monkeypatch.setattr(service, "utcnow", lambda: START + timedelta(hours=1))
    for mode in ("dry_run", "review", "live"):
        assert service.claim_agent_actions(db_session, agent_id="old-agent", mode=mode, capabilities=["taobao_desktop"]) == []
    rows[0].status = "discovery_ready"
    db_session.flush()
    assert service.claim_discovery_actions(db_session, agent_id="old-agent", mode="live", capabilities=["taobao_desktop"]) == []
    rows[1].status, rows[1].first_sent_at = "waiting_reply", START
    db_session.flush()
    assert service.agent_watch_list(db_session, capabilities=["taobao_desktop"]) == []
    assert all(row.execution_attempts == 0 and row.discovery_attempts == 0 for row in rows)


def test_restart_late_messages_cannot_reopen_or_rewrite_cutoff(db_session, monkeypatch):
    task, rows = seeded(db_session)
    batch.activate(db_session, task, activated_by="test", now=START)
    current = START + timedelta(hours=1)
    monkeypatch.setattr(service, "utcnow", lambda: current)
    service.review_inquiry_message(db_session, task, rows[0], content=service.initial_message(task, rows[0]), reviewed_by="test")
    service.mark_message_sent(db_session, task, rows[0], external_message_id="fake-offline-1")
    msg = service.record_reply(db_session, task, rows[0], content="合成报价", quote_complete=True, normalized_unit_price=10)
    db_session.commit()
    # Resume at hour 52: as_of stays hour 48, generated_at records real recovery.
    current = START + timedelta(hours=52)
    db_session.expire_all()
    assert batch.close_due_batches(db_session, now=current) == {"closed": 1, "platform_actions": 0}
    db_session.commit()
    report = deepcopy(task.deadline_report)
    assert report["as_of"] == (START + timedelta(hours=48)).isoformat()
    assert report["generated_at"] == current.isoformat()
    assert report["confirmed_sent_merchants"] == report["replied_merchants"] == 1
    assert report["quote_evidence"][0]["message_id"] == msg.id
    assert report["recommendations_ready"] is False
    late = service.record_reply(db_session, task, rows[0], content="迟到资料", received_at=START + timedelta(hours=2), normalized_unit_price=1)
    db_session.commit()
    assert late.message_meta["late_supplement"] is True
    assert rows[0].normalized_unit_price == 10
    assert task.status == "expired" and task.deadline_report == report
    assert batch.close_due_batches(db_session, now=current)["closed"] == 0
    batch.activate(db_session, task, activated_by="test", now=current)
    assert batch.utc(task.started_at) == START


def test_late_reply_before_scheduler_closes_first_and_is_not_included(db_session, monkeypatch):
    task, rows = seeded(db_session)
    batch.activate(db_session, task, activated_by="test", now=START)
    db_session.commit()
    monkeypatch.setattr(service, "utcnow", lambda: START + timedelta(hours=49))
    msg = service.record_reply(db_session, task, rows[0], content="backdated", received_at=START + timedelta(hours=1), quote_complete=True)
    assert task.deadline_report["evidence_message_ids"] == []
    assert msg.message_meta["late_supplement"]


def test_cancel_keeps_deadline_and_unknown_lease(db_session):
    task, rows = seeded(db_session)
    batch.activate(db_session, task, activated_by="test", now=START)
    rows[0].lease_token = "offline-test-lease"
    rows[0].execution_attempts = 1
    db_session.commit()
    assert batch.close_batch(db_session, task, now=START + timedelta(hours=1), cancel=True)
    assert task.status == "cancelled"
    assert batch.utc(task.deadline_at) == START + timedelta(hours=48)
    assert rows[0].lease_token == "offline-test-lease" and rows[0].execution_attempts == 1
    assert task.deadline_report["confirmed_sent_merchants"] == 0


def test_legacy_task_is_not_started_or_closed(db_session):
    task, _ = seeded(db_session, policy=False)
    assert service.due_actions(db_session, task_id=task.id, now=START)
    with pytest.raises(ValueError, match="旧任务"):
        batch.activate(db_session, task, activated_by="test")
    assert batch.close_due_batches(db_session)["closed"] == 0
    assert task.started_at is None and task.status == "ready"


def test_cancelled_draft_cannot_be_activated(db_session):
    task, _ = seeded(db_session)
    task.status = "cancelled"
    db_session.commit()
    with pytest.raises(ValueError):
        batch.activate(db_session, task, activated_by="test", now=START)
    assert task.started_at is None and task.deadline_at is None


def test_cancelled_draft_reply_cannot_reopen_it(db_session):
    task, rows = seeded(db_session)
    task.status = "cancelled"
    db_session.commit()
    with pytest.raises(ValueError, match="尚未启动"):
        service.record_reply(db_session, task, rows[0], content="历史回复")
    assert task.status == "cancelled"


@pytest.mark.parametrize("close_first", [False, True])
def test_deadline_blocks_winner_and_message_review(db_session, monkeypatch, close_first):
    task, rows = seeded(db_session)
    batch.activate(db_session, task, activated_by="test", now=START)
    db_session.commit()
    current = START + timedelta(hours=49)
    monkeypatch.setattr(service, "utcnow", lambda: current)
    if close_first:
        batch.close_batch(db_session, task, now=current)
        db_session.commit()
    before = (task.status, deepcopy(task.deadline_report))
    with pytest.raises(ValueError, match="窗口"):
        service.apply_winner(db_session, task, "A")
    with pytest.raises(ValueError, match="窗口"):
        service.review_inquiry_message(db_session, task, rows[0], content="不能再发", reviewed_by="test")
    assert (task.status, task.deadline_report) == before


def test_corrupt_receipt_is_excluded_without_blocking_deadline(db_session):
    task, rows = seeded(db_session)
    batch.activate(db_session, task, activated_by="test", now=START)
    task.status = "needs_review"
    db_session.add(ProcurementMessage(
        inquiry_id=rows[0].id, direction="inbound", content="offline bad receipt",
        event_at=START + timedelta(hours=1), message_meta={"server_recorded_at": "invalid"},
    ))
    db_session.commit()
    assert batch.close_due_batches(db_session, now=START + timedelta(hours=49))["closed"] == 1
    assert task.status == "expired"
    assert task.deadline_report["invalid_receipt_count"] == 1
    assert task.deadline_report["evidence_message_ids"] == []


def test_new_tasks_cannot_silently_drop_extra_merchants(db_session):
    task = service.create_task(db_session, {"title": "t", "item_name": "t", "planned_merchant_count": 1}, created_by="test")
    service.review_scripts(db_session, task, script_a="t", script_b=None, reviewed_by="test")
    with pytest.raises(ValueError, match="超过"):
        service.prepare_inquiries(db_session, task, [{"merchant_name": "one"}, {"merchant_name": "two"}])


def test_scheduler_registration_and_callback_are_database_only(db_session, monkeypatch):
    from app.services import scheduler
    monkeypatch.setattr(scheduler, "_REGISTRY", {})
    scheduler._register_default_jobs()
    job = scheduler._REGISTRY["procurement_deadline_1min"]
    assert job["interval_minutes"] == 1
    task, _ = seeded(db_session)
    batch.activate(db_session, task, activated_by="test", now=START)
    monkeypatch.setattr(batch, "now_utc", lambda: START + timedelta(hours=49))
    assert job["fn"](db_session) == {"closed": 1, "platform_actions": 0}


def test_api_activation_freeze_and_permissions():
    engine = sa.create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as db:
        application = FastAPI()
        application.include_router(router)
        application.dependency_overrides[get_db] = lambda: db
        principal = SimpleNamespace(username="test", role="operator")
        application.dependency_overrides[get_current_user] = lambda: principal
        with TestClient(application) as client:
            payload = {"title": "offline", "item_name": "fixture", "specification": "fixture size", "requirements": "offline only", "planned_merchant_count": 50, "generate_scripts": False}
            created = client.post("/api/procurement/tasks", json=payload)
            assert created.status_code == 200, created.text
            data = created.json()
            assert data["planned_merchant_count"] == 50 and data["started_at"] is None
            prefix = f"/api/procurement/tasks/{data['id']}"
            assert client.post(prefix + "/activate").status_code == 409
            assert client.post(prefix + "/review-scripts", json={"script_a": "offline"}).status_code == 200
            queue = client.post(prefix + "/prepare-queue", json={"merchants": []})
            assert len(queue.json()) == 50
            activated = client.post(prefix + "/activate")
            assert activated.status_code == 200, activated.text
            deadline = activated.json()["deadline_at"]
            assert deadline.endswith("Z") or deadline.endswith("+00:00")
            assert client.post(prefix + "/activate").json()["deadline_at"] == deadline
            assert client.patch(prefix, json={"quantity": 99}).status_code == 409
            assert client.patch(prefix, json={"status": "ready"}).status_code == 409
            assert client.patch(f"/api/procurement/inquiries/{queue.json()[0]['id']}", json={"merchant_name": "replacement"}).status_code == 409
            principal.role = "viewer"
            assert client.post(prefix + "/activate").status_code == 403
    engine.dispose()


def test_additive_migration_keeps_legacy_null_and_rejects_lossy_downgrade():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0155_procurement_batch_window.py"
    spec = importlib.util.spec_from_file_location("procurement_migration_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE procurement_tasks (id INTEGER PRIMARY KEY, title VARCHAR(128))"))
        conn.execute(sa.text("INSERT INTO procurement_tasks VALUES (1, 'legacy')"))
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
            module.upgrade()
            assert conn.execute(sa.text("SELECT started_at, batch_policy_version FROM procurement_tasks")).one() == (None, None)
            conn.execute(sa.text("UPDATE procurement_tasks SET batch_policy_version='48h-v1' WHERE id=1"))
            with pytest.raises(RuntimeError, match="禁止丢失"):
                module.downgrade()
    engine.dispose()
