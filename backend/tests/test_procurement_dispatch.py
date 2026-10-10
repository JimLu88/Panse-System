"""Offline protocol tests: synthetic identities, no API/platform/desktop calls."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
from threading import Barrier
from uuid import uuid4
from types import SimpleNamespace

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models import Base
from app.models.procurement import ProcurementInquiry, ProcurementMessage, ProcurementTask
from app.models.procurement_dispatch import ProcurementSendIntent as Intent, ProcurementContactClaim as Contact
from app.services import procurement_service as legacy, procurement_batch_service as batch, procurement_dispatch_service as dispatch
from app.api.procurement import router
from app.database import get_db
from app.dependencies import get_current_user

START = datetime(2026, 9, 26, 2, 0, tzinfo=timezone.utc)  # Beijing 10:00
EXECUTOR = "offline-pc"


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(legacy, "utcnow", lambda: START)
    monkeypatch.setattr(batch, "now_utc", lambda: START)


def account(db, channel="taobao", *, input_group="fixture-desktop", **caps):
    row = dispatch.register_account(db, channel=channel, identity_hash=dispatch.digest(str(uuid4())),
                                    executor_id=EXECUTOR, input_group=input_group, **caps)
    assert row.status == "paused"
    # Test setup ONLY. No production enable endpoint exists in this slice.
    row.status = "ready"
    db.commit()
    return row


def task_rows(db, accounts, count=2):
    channels = [a.channel for a in accounts]
    task = legacy.create_task(db, {"title": "离线发送协议", "item_name": "合成配件",
        "specification": "测试规格", "requirements": "不外发", "quantity": 2,
        "channels": channels, "planned_merchant_count": count, "ab_test_enabled": False,
        "max_followup_rounds": 2, "batch_policy_version": batch.POLICY_VERSION}, created_by="test")
    legacy.review_scripts(db, task, script_a="您好，合成测试资料，不外发。", script_b=None, reviewed_by="test")
    prefix = str(uuid4())
    rows = legacy.prepare_inquiries(db, task, [{"channel": channels[i % len(channels)],
        "merchant_name": f"合成商家{i}", "merchant_external_id": f"{prefix}-{i}"} for i in range(count)])
    db.commit()
    for a in accounts:
        dispatch.bind_task(db, task_id=task.id, account_id=a.id)
    db.commit()
    batch.activate(db, task, activated_by="test", now=START)
    db.commit()
    for row in rows:
        legacy.review_inquiry_message(db, task, row, content=legacy.initial_message(task, row), reviewed_by="test")
    db.commit()
    return task, rows


def reserve(db, task, row, a, *, now=START, content=None, key="initial:0"):
    result = dispatch.reserve(db, task_id=task.id, inquiry_id=row.id, account_id=a.id,
        executor_id=EXECUTOR, action_key=key, content=content or row.approved_message, now=now)
    db.commit()
    return result


def permit(db, intent, *, now=START):
    result = dispatch.issue_permit(db, intent_id=intent.id, executor_id=EXECUTOR, now=now)
    db.commit()
    return result


def confirm(db, intent, token, *, now=START + timedelta(seconds=1)):
    row = dispatch.confirm_sent(db, intent_id=intent.id, executor_id=EXECUTOR, permit=token["permit"],
        payload_hash=intent.payload_hash, external_message_id="offline-msg-" + intent.id,
        observed_at=START + timedelta(seconds=1) if now >= START + timedelta(hours=48) else now, now=now)
    db.commit()
    return row


def test_binding_snapshot_stable_and_paused_by_default(db_session):
    a = account(db_session)
    task, _ = task_rows(db_session, [a])
    snapshot = task.policy_snapshot["account_bindings"][0]
    assert snapshot["account_id"] == a.id and snapshot["new_24h"] == 25
    assert task.policy_snapshot["agent_execution_ready"] is False
    with pytest.raises(ValueError):
        dispatch.bind_task(db_session, task_id=task.id, account_id=a.id)
    db_session.rollback()


@pytest.mark.parametrize("caps", [{"new_24h": 26}, {"new_1h": 7}, {"messages_24h": 41}, {"new_1h": True}, {"new_1h": 0}])
def test_account_caps_only_lowered(db_session, caps):
    with pytest.raises(dispatch.DispatchBlocked):
        account(db_session, **caps)


def test_two_tasks_share_hourly_budget(db_session):
    a = account(db_session, new_1h=1)
    first, rows = task_rows(db_session, [a], 1)
    second, other = task_rows(db_session, [a], 1)
    intent = reserve(db_session, first, rows[0], a)
    with pytest.raises(dispatch.DispatchBlocked, match="额度"):
        reserve(db_session, second, other[0], a)
    db_session.rollback()
    confirm(db_session, intent, permit(db_session, intent))
    usage = dispatch.budget_status(db_session, a.id, now=START + timedelta(minutes=1))["used"]
    assert usage == {"new_24h": 1, "new_1h": 1, "messages_24h": 1}
    assert reserve(db_session, second, other[0], a, now=START + timedelta(hours=1)).state == "reserved"


@pytest.mark.parametrize("cap", ["new_24h", "messages_24h"])
def test_day_budget_is_cross_task_and_attempts_charge_once(db_session, cap):
    a = account(db_session, **{cap: 1})
    t1, r1 = task_rows(db_session, [a], 1)
    t2, r2 = task_rows(db_session, [a], 1)
    intent = reserve(db_session, t1, r1[0], a)
    grant = permit(db_session, intent)
    confirm(db_session, intent, grant)
    confirm(db_session, intent, grant)  # callback retries cannot charge twice
    with pytest.raises(dispatch.DispatchBlocked, match="额度"):
        reserve(db_session, t2, r2[0], a, now=START + timedelta(hours=1))
    db_session.rollback()
    assert dispatch.budget_status(db_session, a.id, now=START + timedelta(hours=24))["used"][cap] == 0
    reserve(db_session, t2, r2[0], a, now=START + timedelta(hours=24))
    assert db_session.scalar(sa.select(sa.func.count()).select_from(ProcurementMessage)) == 1


def test_idempotent_reserve_payload_tamper_and_no_second_permit(db_session):
    a = account(db_session)
    t, rows = task_rows(db_session, [a])
    i = reserve(db_session, t, rows[0], a)
    assert reserve(db_session, t, rows[0], a).id == i.id
    with pytest.raises(dispatch.DispatchBlocked, match="内容"):
        reserve(db_session, t, rows[0], a, content="篡改文案")
    db_session.rollback()
    grant = permit(db_session, i)
    assert datetime.fromisoformat(grant["expires_at"]) == START + timedelta(seconds=5)
    with pytest.raises(dispatch.DispatchBlocked, match="再次"):
        permit(db_session, i)
    db_session.rollback()
    assert i.attempt_at is not None and i.permit_hash != grant["permit"]


@pytest.mark.parametrize("beijing_hour,allowed", [(8, False), (9, True), (19, True), (20, False), (23, False)])
def test_quiet_hours(db_session, beijing_hour, allowed):
    a = account(db_session)
    t, rows = task_rows(db_session, [a])
    now = START + timedelta(days=1, hours=beijing_hour - 10)
    if allowed:
        reserve(db_session, t, rows[0], a, now=now)
    else:
        with pytest.raises(dispatch.DispatchBlocked, match="安静"):
            reserve(db_session, t, rows[0], a, now=now)


def test_five_second_stop_boundary(db_session):
    a = account(db_session)
    t, rows = task_rows(db_session, [a])
    now = START + timedelta(hours=48, seconds=-7)
    i = reserve(db_session, t, rows[0], a, now=now)
    grant = permit(db_session, i, now=now)
    assert datetime.fromisoformat(grant["expires_at"]) == START + timedelta(hours=48, seconds=-5)
    with pytest.raises(dispatch.DispatchBlocked, match="停止"):
        reserve(db_session, t, rows[1], a, now=START + timedelta(hours=48, seconds=-5))


def test_pre_attempt_reservation_can_expire_but_not_rearm(db_session):
    a = account(db_session, new_1h=1)
    t, rows = task_rows(db_session, [a])
    i = reserve(db_session, t, rows[0], a)
    later = START + timedelta(seconds=60)
    dispatch.recover_account(db_session, account_id=a.id, now=later)
    db_session.commit()
    assert i.state == "cancelled" and i.attempt_at is None
    assert reserve(db_session, t, rows[0], a, now=later).id == i.id
    assert i.state == "cancelled"
    assert dispatch.budget_status(db_session, a.id, now=later)["used"]["new_1h"] == 0
    reserve(db_session, t, rows[1], a, now=later)


def test_unknown_one_lookup_crash_unresolved_late_confirmation(db_session):
    a = account(db_session)
    t, rows = task_rows(db_session, [a])
    i = reserve(db_session, t, rows[0], a)
    grant = permit(db_session, i)
    dispatch.recover_account(db_session, account_id=a.id, now=START + timedelta(seconds=6))
    db_session.commit()
    assert i.state == "unknown"
    with pytest.raises(dispatch.DispatchBlocked, match="未决"):
        reserve(db_session, t, rows[1], a, now=START + timedelta(seconds=6))
    db_session.rollback()
    dispatch.begin_reconcile(db_session, intent_id=i.id, executor_id=EXECUTOR, now=START + timedelta(seconds=6))
    db_session.commit()
    db_session.expire_all()  # restarted reader, not a new lookup budget
    dispatch.recover_account(db_session, account_id=a.id, now=START + timedelta(minutes=1))
    db_session.commit()
    assert i.state == "unresolved" and i.reconcile_attempts == 1
    with pytest.raises(dispatch.DispatchBlocked, match="一次"):
        dispatch.begin_reconcile(db_session, intent_id=i.id, executor_id=EXECUTOR)
    db_session.rollback()
    with pytest.raises(dispatch.DispatchBlocked):
        dispatch.cancel_reserved(db_session, intent_id=i.id, executor_id=EXECUTOR)
    db_session.rollback()
    batch.close_batch(db_session, t, now=START + timedelta(hours=49))
    db_session.commit()
    report = deepcopy(t.deadline_report)
    attempt = i.attempt_at
    confirm(db_session, i, grant, now=START + timedelta(hours=49))
    assert i.state == "confirmed_sent" and i.attempt_at == attempt
    assert i.reconcile_attempts == 1 and t.deadline_report == report
    assert db_session.scalar(sa.select(ProcurementMessage)).message_meta["late_supplement"] is True
    assert db_session.get(Contact, i.contact_id).attempted_at is not None


def test_shared_input_group_blocks_different_platform(db_session):
    a, b = account(db_session), account(db_session, "1688")
    t, rows = task_rows(db_session, [a, b])
    i = reserve(db_session, t, rows[0], a)
    j = reserve(db_session, t, rows[1], b)
    permit(db_session, i)
    with pytest.raises(dispatch.DispatchBlocked, match="未决"):
        permit(db_session, j)


@pytest.mark.parametrize("change", ["pause", "cancel", "text", "identity", "owner"])
def test_recheck_before_permission(db_session, change):
    a = account(db_session)
    t, rows = task_rows(db_session, [a])
    i = reserve(db_session, t, rows[0], a)
    if change == "pause": a.status = "paused"
    elif change == "cancel": t.status = "cancelled"
    elif change == "text": rows[0].approved_message = "different"
    elif change == "identity": rows[0].merchant_external_id = "different-shop"
    elif change == "owner": a.executor_id = "another-pc"
    db_session.commit()
    with pytest.raises(dispatch.DispatchBlocked):
        permit(db_session, i)
    db_session.rollback()
    assert i.state == "reserved" and i.attempt_at is None


def test_unknown_never_frees_batch_merchant_count(db_session):
    a = account(db_session)
    t, rows = task_rows(db_session, [a], 1)
    i = reserve(db_session, t, rows[0], a)
    confirm(db_session, i, permit(db_session, i))
    # Simulate a buggy upstream producer inserting an extra slot despite frozen N.
    extra = ProcurementInquiry(task_id=t.id, channel="taobao", slot_no=2,
        merchant_external_id="extra-shop", message_variant="A", status="ready")
    db_session.add(extra)
    db_session.flush()
    legacy.review_inquiry_message(db_session, t, extra, content=legacy.initial_message(t, extra), reviewed_by="test")
    db_session.commit()
    with pytest.raises(dispatch.DispatchBlocked, match="名额"):
        reserve(db_session, t, extra, a)


def test_unanswered_followup_not_early_and_at_most_once(db_session):
    a = account(db_session)
    t, rows = task_rows(db_session, [a], 1)
    i = reserve(db_session, t, rows[0], a)
    confirm(db_session, i, permit(db_session, i))
    row = rows[0]
    row.status = "followup_ready"
    legacy.review_inquiry_message(db_session, t, row, content=legacy.followup_message(t, row), reviewed_by="test")
    db_session.commit()
    with pytest.raises(dispatch.DispatchBlocked, match="24小时"):
        reserve(db_session, t, row, a, now=START + timedelta(hours=1), key="followup:1")
    db_session.rollback()
    later = START + timedelta(hours=24)
    j = reserve(db_session, t, row, a, now=later, key="followup:1")
    confirm(db_session, j, permit(db_session, j, now=later), now=later + timedelta(seconds=1))
    row.status = "followup_ready"
    legacy.review_inquiry_message(db_session, t, row, content=legacy.followup_message(t, row), reviewed_by="test")
    db_session.commit()
    with pytest.raises(dispatch.DispatchBlocked, match="一次"):
        reserve(db_session, t, row, a, now=later + timedelta(hours=1), key="followup:2")


def test_fifty_synthetic_merchants_with_two_accounts(db_session):
    a, b = account(db_session), account(db_session, "1688")
    t, rows = task_rows(db_session, [a, b], 50)
    for n, row in enumerate(rows):
        at = START + timedelta(hours=n // 10, minutes=n % 10)
        i = reserve(db_session, t, row, a if n % 2 == 0 else b, now=at)
        confirm(db_session, i, permit(db_session, i, now=at), now=at + timedelta(seconds=1))
    assert db_session.scalar(sa.select(sa.func.count()).select_from(Contact)) == 50
    assert db_session.scalar(sa.select(sa.func.count()).select_from(Intent).where(Intent.state == "confirmed_sent")) == 50
    batch.close_batch(db_session, t, now=START + timedelta(hours=48))
    assert t.deadline_report["confirmed_sent_merchants"] == 50
    for binding in (a, b):
        usage = dispatch.budget_status(db_session, binding.id, now=START + timedelta(hours=5))["used"]
        assert usage["new_24h"] == usage["messages_24h"] == 25


@pytest.mark.parametrize("field,value", [("permit", "wrong"), ("permit", None), ("payload_hash", "wrong"), ("external_message_id", "manual-review-123"), ("external_message_id", "human-review-123"), ("external_message_id", "   "), ("observed_at", None)])
def test_receipt_requires_original_permission_and_matching_evidence(db_session, field, value):
    a = account(db_session)
    t, rows = task_rows(db_session, [a], 1)
    i = reserve(db_session, t, rows[0], a)
    token = permit(db_session, i)
    payload = {"intent_id": i.id, "executor_id": EXECUTOR, "permit": token["permit"],
               "payload_hash": i.payload_hash, "external_message_id": "offline-real-shape-id",
               "observed_at": START + timedelta(seconds=1), "now": START + timedelta(seconds=1)}
    payload[field] = value
    with pytest.raises(dispatch.DispatchBlocked):
        dispatch.confirm_sent(db_session, **payload)
    db_session.rollback()
    assert i.state == "executing"
    assert db_session.scalar(sa.select(sa.func.count()).select_from(ProcurementMessage)) == 0


def test_cancel_before_permit_releases_only_unattempted_budget(db_session):
    a = account(db_session, new_1h=1)
    t, rows = task_rows(db_session, [a])
    i = reserve(db_session, t, rows[0], a)
    dispatch.cancel_reserved(db_session, intent_id=i.id, executor_id=EXECUTOR)
    db_session.commit()
    assert dispatch.budget_status(db_session, a.id, now=START)["used"]["messages_24h"] == 0
    j = reserve(db_session, t, rows[1], a)
    permit(db_session, j)
    with pytest.raises(dispatch.DispatchBlocked):
        dispatch.cancel_reserved(db_session, intent_id=j.id, executor_id=EXECUTOR)


def test_same_account_cannot_be_aliased_to_bypass_quota(db_session):
    a = account(db_session)
    with pytest.raises(sa.exc.IntegrityError):
        dispatch.register_account(db_session, channel=a.channel, identity_hash=a.identity_hash,
                                  executor_id="other-pc", input_group="other-group")
    db_session.rollback()


def test_unknown_holds_batch_slot_even_on_other_account_and_input_group(db_session):
    a = account(db_session)
    b = account(db_session, "1688", input_group="second-fixture-desktop")
    t, rows = task_rows(db_session, [a, b], 1)
    i = reserve(db_session, t, rows[0], a)
    permit(db_session, i)
    dispatch.mark_unknown(db_session, intent_id=i.id, executor_id=EXECUTOR)
    extra = ProcurementInquiry(task_id=t.id, channel="1688", slot_no=2,
        merchant_external_id="extra-shop", message_variant="A", status="ready")
    db_session.add(extra)
    db_session.flush()
    legacy.review_inquiry_message(db_session, t, extra, content=legacy.initial_message(t, extra), reviewed_by="test")
    db_session.commit()
    with pytest.raises(dispatch.DispatchBlocked, match="名额"):
        reserve(db_session, t, extra, b)
    db_session.rollback()
    assert i.state == "unknown" and db_session.get(Contact, i.contact_id).attempted_at is not None


def test_clock_rollback_is_not_free_budget(db_session):
    a = account(db_session)
    t, rows = task_rows(db_session, [a], 1)
    i = reserve(db_session, t, rows[0], a, now=START + timedelta(minutes=2))
    permit(db_session, i, now=START + timedelta(minutes=2))
    with pytest.raises(dispatch.DispatchBlocked, match="时间回退"):
        dispatch.budget_status(db_session, a.id, now=START)


def test_missing_stable_merchant_identity_cannot_reserve(db_session):
    a = account(db_session)
    t, rows = task_rows(db_session, [a], 1)
    rows[0].merchant_external_id = "   "
    db_session.commit()
    with pytest.raises(dispatch.DispatchBlocked, match="身份"):
        reserve(db_session, t, rows[0], a)


def test_read_only_status_api_has_no_content_or_permit():
    engine = sa.create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, autoflush=False)() as db:
        a = account(db)
        t, rows = task_rows(db, [a], 1)
        i = reserve(db, t, rows[0], a)
        grant = permit(db, i)
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = lambda: db
        principal = SimpleNamespace(username="test", role="operator")
        app.dependency_overrides[get_current_user] = lambda: principal
        before = i.state
        with TestClient(app) as client:
            response = client.get(f"/api/procurement/tasks/{t.id}/dispatch-status")
            assert response.status_code == 200
            data = response.json()
            assert data["external_send_enabled"] is False and data["occupied_merchants"] == 1
            assert i.content not in response.text and grant["permit"] not in response.text
            assert i.state == before  # GET never recovers, mutates, or enables anything.
            principal.role = "viewer"
            assert client.get(f"/api/procurement/tasks/{t.id}/dispatch-status").status_code == 403
    engine.dispose()


@pytest.mark.parametrize("phase", ["reserve", "permit", "reconcile"])
def test_concurrent_connections_cannot_double_consume(tmp_path, phase):
    engine = sa.create_engine("sqlite:///" + str(tmp_path / "dispatch.sqlite"), connect_args={"check_same_thread": False, "timeout": 10})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with Session() as db:
        a = account(db, new_1h=1)
        t1, r1 = task_rows(db, [a], 1)
        t2, r2 = task_rows(db, [a], 1)
        data = [(t1.id, r1[0].id, r1[0].approved_message), (t2.id, r2[0].id, r2[0].approved_message)]
        account_id = a.id
        if phase != "reserve":
            i = reserve(db, t1, r1[0], a)
            intent_id = i.id
            if phase == "reconcile":
                permit(db, i)
                dispatch.mark_unknown(db, intent_id=i.id, executor_id=EXECUTOR)
                db.commit()
    barrier = Barrier(2)

    def compete(index):
        with Session() as db:
            barrier.wait(timeout=10)
            try:
                if phase == "reserve":
                    task_id, inquiry_id, content = data[index]
                    dispatch.reserve(db, task_id=task_id, inquiry_id=inquiry_id, account_id=account_id,
                        executor_id=EXECUTOR, action_key="initial:0", content=content, now=START)
                elif phase == "permit":
                    dispatch.issue_permit(db, intent_id=intent_id, executor_id=EXECUTOR, now=START)
                else:
                    dispatch.begin_reconcile(db, intent_id=intent_id, executor_id=EXECUTOR, now=START + timedelta(seconds=6))
                db.commit()
                return "accepted"
            except dispatch.DispatchBlocked:
                db.rollback()
                return "blocked"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(compete, range(2)))
    assert sorted(results) == ["accepted", "blocked"]
    with Session() as db:
        assert db.scalar(sa.select(sa.func.count()).select_from(Intent)) == 1
        if phase == "reconcile":
            assert db.get(Intent, intent_id).reconcile_attempts == 1
    engine.dispose()


def test_shared_group_permits_are_serial_across_accounts(tmp_path):
    engine = sa.create_engine("sqlite:///" + str(tmp_path / "group.sqlite"), connect_args={"check_same_thread": False, "timeout": 10})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with Session() as db:
        a, b = account(db), account(db, "1688")
        task, rows = task_rows(db, [a, b])
        ids = [reserve(db, task, rows[0], a).id, reserve(db, task, rows[1], b).id]
    barrier = Barrier(2)

    def claim(intent_id):
        with Session() as db:
            barrier.wait(timeout=10)
            try:
                dispatch.issue_permit(db, intent_id=intent_id, executor_id=EXECUTOR, now=START)
                db.commit()
                return True
            except dispatch.DispatchBlocked:
                db.rollback()
                return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(claim, ids)) == [False, True]
    engine.dispose()


def test_migration_adds_only_dispatch_tables_and_preserves_ledger():
    path = Path(__file__).resolve().parents[1] / "alembic/versions/0156_procurement_dispatch_ledger.py"
    spec = importlib.util.spec_from_file_location("dispatch_migration", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE procurement_tasks (id INTEGER PRIMARY KEY)"))
        conn.execute(sa.text("CREATE TABLE procurement_inquiries (id INTEGER PRIMARY KEY)"))
        with Operations.context(MigrationContext.configure(conn)):
            mod.upgrade()
            mod.upgrade()
            tables = set(sa.inspect(conn).get_table_names())
            assert len(tables) == 7
            for name in tables - {"procurement_tasks", "procurement_inquiries"}:
                assert {c["name"] for c in sa.inspect(conn).get_columns(name)} == set(Base.metadata.tables[name].c.keys())
            with pytest.raises(RuntimeError, match="保留"):
                mod.downgrade()
    engine.dispose()
