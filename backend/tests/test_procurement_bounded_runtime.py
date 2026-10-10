"""Local journal -> HTTP contract -> scratch ERP DB; never a real platform."""
from copy import deepcopy
from datetime import timedelta
import os
from pathlib import Path
import sqlite3
import sys
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.procurement_agent.bounded_runtime import BoundedDispatcher, WindowsPermitProtector
from tools.procurement_agent.client import ProcurementApiClient, AgentApiError
from tools.procurement_agent.send_journal import SendJournal, SendBlocked
from tools.procurement_agent.drivers import MockDriver
from app.api.procurement_agent import router, require_agent_token
from app.database import get_db
from app.models import Base
from app.models.procurement import ProcurementMessage
from app.models.procurement_dispatch import ProcurementSendIntent as Intent
from app.services import procurement_batch_service as batch, procurement_service as legacy
from backend.tests.test_procurement_dispatch import START, EXECUTOR, account, task_rows


class OfflineCipher:
    """Test-only codec, never wired by production configuration."""
    def seal(self, value): return b"offline:" + value[::-1].encode()
    def open(self, value): return value[8:].decode()[::-1]


class OfflineTransport(ProcurementApiClient):
    def __init__(self, api):
        super().__init__("https://offline.invalid", "synthetic", executor_id=EXECUTOR)
        self.api, self.calls = api, []
        self.lost_operation = None

    def _request(self, method, path, payload=None):
        self.calls.append(path.rsplit("/", 1)[-1])
        response = self.api.request(method, path, json=payload,
                                    headers={"X-Procurement-Executor": self.executor_id})
        if response.status_code != 200:
            raise AgentApiError(str(response.status_code))
        if path.endswith("/" + str(self.lost_operation)):
            self.lost_operation = None
            raise AgentApiError("synthetic lost response AFTER commit")
        return response.json()


class OfflineDriver:
    protocol = "dispatch-v1"
    def __init__(self, journal, action):
        self.journal, self.action = journal, action
        self.prepares = self.clicks = self.lookups = 0
        self.fail_after_click = self.no_lookup_result = self.raise_lookup = False
        self.before = lambda: None
        self.proof_override = {}

    def proof(self, action):
        return {**{k: action[k] for k in ("channel", "account_identity_hash", "merchant_external_id")},
                "input_owned": True, "access_gate": False, "sent_content": action["suggested_message"],
                "direction": "outbound",
                "external_message_id": "offline-platform-" + str(action["inquiry_id"]),
                "observed_at": START.isoformat(), **self.proof_override}

    def prepare(self, action):
        self.prepares += 1
        self.journal.require_only(action)
        assert self.journal.state(action) == "executing"
        return self.proof(action)

    def commit(self, action, before_click):
        self.before()
        before_click(self.proof(action))
        assert self.journal.state(action) == "executing"
        self.clicks += 1
        if self.fail_after_click:
            raise TimeoutError("synthetic crash after click")
        return self.proof(action)

    def reconcile(self, action):
        self.lookups += 1
        if self.raise_lookup:
            raise TimeoutError("synthetic read failure")
        return None if self.no_lookup_result else self.proof(action)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(batch, "now_utc", lambda: START)
    monkeypatch.setattr(legacy, "utcnow", lambda: START)
    monkeypatch.setenv("PROCUREMENT_DISPATCH_EXECUTOR_ID", EXECUTOR)
    monkeypatch.setenv("PROCUREMENT_DISPATCH_ENABLED", "1")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        a = account(db)
        task, inquiries = task_rows(db, [a], 2)
        row = inquiries[0]
        action = {"task_id": task.id, "inquiry_id": row.id, "action_key": "initial:0",
                  "account_id": a.id, "executor_id": EXECUTOR, "input_group": a.input_group,
                  "channel": a.channel, "account_identity_hash": a.identity_hash,
                  "merchant_external_id": row.merchant_external_id,
                  "suggested_message": row.approved_message, "deadline_at": batch.utc(task.deadline_at).isoformat()}
    app = FastAPI()
    app.include_router(router)
    def db_dependency():
        with factory() as session:
            yield session
    app.dependency_overrides[get_db] = db_dependency
    app.dependency_overrides[require_agent_token] = lambda: "synthetic"
    with TestClient(app) as api:
        transport = OfflineTransport(api)
        journal = SendJournal(tmp_path / "local.sqlite", server_scope="https://offline.invalid")
        tick = [100.0]
        stopped = [False]
        def dispatcher():
            return BoundedDispatcher(journal, transport, protector=OfflineCipher(),
                                     monotonic=lambda: tick[0], stopped=lambda: stopped[0])
        driver = OfflineDriver(journal, action)
        yield dispatcher, driver, action, transport, factory, tick, stopped
    engine.dispose()


def test_http_end_to_end_once_and_no_plaintext_local_journal(setup):
    make, driver, action, transport, dbf, *_ = setup
    d = make()
    assert d.send_once(action, driver)["phase"] == "confirmed"
    assert driver.clicks == 1 and driver.lookups == 0
    assert d.recover_once(action, driver)["phase"] == "confirmed"
    with pytest.raises(SendBlocked): make().send_once(action, driver)
    with dbf() as db:
        assert db.scalar(select(func.count()).select_from(ProcurementMessage)) == 1
        assert db.scalar(select(Intent)).state == "confirmed_sent"
    data = d.journal.path.read_bytes()
    assert action["suggested_message"].encode() not in data
    assert action["merchant_external_id"].encode() not in data
    assert d.status(action) == {"phase": "confirmed", "server_intent": d.status(action)["server_intent"], "reconcile_used": 0}


def test_lost_callback_ack_replays_receipt_only_after_restart_and_stop(setup, monkeypatch):
    make, driver, action, transport, dbf, _, stopped = setup
    transport.lost_operation = "receipt"
    with pytest.raises(AgentApiError): make().send_once(action, driver)
    assert make().status(action)["phase"] == "receipt"
    stopped[0] = True
    monkeypatch.setenv("PROCUREMENT_DISPATCH_ENABLED", "0")
    assert make().recover_once(action, driver)["phase"] == "confirmed"
    assert driver.clicks == 1 and driver.lookups == 0
    assert transport.calls.count("permit") == 1
    with dbf() as db:
        assert db.scalar(select(func.count()).select_from(ProcurementMessage)) == 1


def test_unknown_one_read_recovery_and_old_journal_interlock(setup):
    make, driver, action, transport, dbf, *_ = setup
    driver.fail_after_click = True
    with pytest.raises(TimeoutError): make().send_once(action, driver)
    assert make().status(action)["phase"] == "unknown"
    with pytest.raises(SendBlocked): driver.journal.require_clear_desktop()
    with pytest.raises(SendBlocked): driver.journal.begin({**action, "inquiry_id": 555})
    assert make().recover_once(action, driver)["phase"] == "confirmed"
    assert driver.lookups == driver.clicks == 1
    driver.journal.require_clear_desktop()
    with dbf() as db:
        assert db.scalar(select(Intent)).reconcile_attempts == 1


@pytest.mark.parametrize("raises", [False, True])
def test_unknown_unresolved_never_second_lookup(setup, raises):
    make, driver, action, transport, dbf, *_ = setup
    driver.fail_after_click = True
    with pytest.raises(TimeoutError): make().send_once(action, driver)
    driver.no_lookup_result, driver.raise_lookup = True, raises
    if raises:
        with pytest.raises(TimeoutError): make().recover_once(action, driver)
    else:
        assert make().recover_once(action, driver)["phase"] == "unresolved"
    with pytest.raises(SendBlocked): make().recover_once(action, driver)
    assert driver.lookups == driver.clicks == 1
    with dbf() as db:
        assert db.scalar(select(Intent)).state == "unresolved"
        assert db.scalar(select(func.count()).select_from(ProcurementMessage)) == 0


def test_lost_reconcile_response_consumes_chance_without_platform_read(setup):
    make, driver, action, transport, dbf, *_ = setup
    driver.fail_after_click = True
    with pytest.raises(TimeoutError): make().send_once(action, driver)
    transport.lost_operation = "reconcile"
    with pytest.raises(AgentApiError): make().recover_once(action, driver)
    with pytest.raises(SendBlocked): make().recover_once(action, driver)
    assert driver.lookups == 0 and driver.clicks == 1
    with dbf() as db: assert db.scalar(select(Intent)).state == "unresolved"


@pytest.mark.parametrize("operation", ["reserve", "permit"])
def test_lost_pre_send_responses_never_request_second_grant(setup, operation):
    make, driver, action, transport, dbf, *_ = setup
    transport.lost_operation = operation
    with pytest.raises(AgentApiError): make().send_once(action, driver)
    with pytest.raises(SendBlocked): make().recover_once(action, driver)
    with pytest.raises(SendBlocked): make().send_once(action, driver)
    assert driver.clicks == driver.lookups == 0
    assert transport.calls.count(operation) == 1


@pytest.mark.parametrize("jump", [5.0, -1.0, float("inf"), float("nan")])
def test_final_monotonic_check_blocks_expiry_or_clock_invalid(setup, jump):
    make, driver, action, _, _, tick, _ = setup
    driver.before = lambda: tick.__setitem__(0, 100 + jump)
    with pytest.raises(SendBlocked): make().send_once(action, driver)
    assert driver.clicks == 0


def test_stop_after_prepare_before_click_blocks_send(setup):
    make, driver, action, _, _, _, stopped = setup
    driver.before = lambda: stopped.__setitem__(0, True)
    with pytest.raises(SendBlocked): make().send_once(action, driver)
    assert driver.clicks == 0


@pytest.mark.parametrize("override", [
    {"merchant_external_id": "wrong"}, {"account_identity_hash": "0" * 64},
    {"access_gate": True}, {"input_owned": False}, {"channel": "1688"},
])
def test_target_identity_or_verification_gate_blocks_before_permit(setup, override):
    make, driver, action, transport, *_ = setup
    driver.proof_override = override
    with pytest.raises(SendBlocked): make().send_once(action, driver)
    assert driver.clicks == 0 and "permit" not in transport.calls


@pytest.mark.parametrize("override", [
    {"external_message_id": "HUMAN-REVIEW-123"}, {"external_message_id": ""},
    {"sent_content": "changed"}, {"observed_at": "2026-09-26T02:00:00"},
    {"direction": "inbound"}, {"direction": None},
])
def test_bad_platform_receipt_stays_unknown(setup, override):
    make, driver, action, _, dbf, *_ = setup
    driver.proof_override = override
    with pytest.raises((ValueError, SendBlocked)): make().send_once(action, driver)
    assert driver.clicks == 1 and make().status(action)["phase"] == "unknown"
    with dbf() as db: assert db.scalar(select(func.count()).select_from(ProcurementMessage)) == 0


def test_changed_action_on_restart_cannot_retarget(setup):
    make, driver, action, transport, *_ = setup
    transport.lost_operation = "receipt"
    with pytest.raises(AgentApiError): make().send_once(action, driver)
    for key in ("suggested_message", "merchant_external_id", "account_id", "input_group"):
        with pytest.raises(SendBlocked): make().recover_once({**action, key: "tampered"}, driver)
    assert driver.clicks == 1 and driver.lookups == 0


@pytest.mark.parametrize("field,value", [("merchant_external_id", "wrong-shop"),
    ("account_identity_hash", "f" * 64), ("executor_id", "wrong-pc"), ("input_group", "wrong-group")])
def test_local_action_cannot_override_server_target(setup, field, value):
    make, driver, action, transport, *_ = setup
    with pytest.raises(SendBlocked): make().send_once({**action, field: value}, driver)
    assert driver.prepares == driver.clicks == 0 and "permit" not in transport.calls


def test_target_switch_between_prepare_and_click_stops(setup):
    make, driver, action, *_ = setup
    driver.before = lambda: driver.proof_override.update(merchant_external_id="another-shop")
    with pytest.raises(SendBlocked): make().send_once(action, driver)
    assert driver.clicks == 0


def test_legacy_driver_and_stopped_rejected_without_reserve(setup):
    make, driver, action, transport, _, _, stopped = setup
    with pytest.raises(SendBlocked): make().send_once(action, MockDriver())
    stopped[0] = True
    with pytest.raises(SendBlocked): make().send_once(action, driver)
    assert not transport.calls


def test_encryption_failure_no_click_and_no_plaintext_fallback(setup):
    make, driver, action, transport, *_ = setup
    d = make()
    def fail(_): raise OSError("synthetic DPAPI failure")
    d.protector.seal = fail
    with pytest.raises(OSError): d.send_once(action, driver)
    assert driver.clicks == 0
    with pytest.raises(SendBlocked): make().recover_once(action, driver)


def test_executing_write_failure_blocks_click(setup):
    make, driver, action, *_ = setup
    d = make()
    original = d._save
    def fail_write(action, phase, **kwargs):
        if phase == "executing":
            raise sqlite3.OperationalError("synthetic disk full")
        return original(action, phase, **kwargs)
    d._save = fail_write
    with pytest.raises(sqlite3.OperationalError): d.send_once(action, driver)
    assert driver.clicks == 0


def test_guard_is_single_use_even_inside_driver(setup):
    make, driver, action, *_ = setup
    def double_attempt(action, before_click):
        before_click(driver.proof(action))
        driver.clicks += 1
        before_click(driver.proof(action))
        driver.clicks += 1
        return driver.proof(action)
    driver.commit = double_attempt
    with pytest.raises(SendBlocked): make().send_once(action, driver)
    assert driver.clicks == 1


def test_missing_final_guard_cannot_claim_success(setup):
    make, driver, action, *_ = setup
    driver.commit = lambda action, before_click: driver.proof(action)
    with pytest.raises(SendBlocked): make().send_once(action, driver)
    assert make().status(action)["phase"] == "unknown"


def test_original_local_lock_prevents_parallel_bounded_driver(setup):
    make, driver, action, transport, *_ = setup
    with driver.journal.execution_lock():
        with pytest.raises(SendBlocked): make().send_once(action, driver)
    assert not transport.calls and driver.clicks == 0


def test_late_callback_recovery_does_not_rewrite_deadline_report(setup, monkeypatch):
    from app.models.procurement import ProcurementTask
    make, driver, action, transport, dbf, *_ = setup
    transport.lost_operation = "receipt"
    with pytest.raises(AgentApiError): make().send_once(action, driver)
    late = START + timedelta(hours=49)
    with dbf() as db:
        task = db.get(ProcurementTask, action["task_id"])
        batch.close_batch(db, task, now=late)
        db.commit()
        report = deepcopy(task.deadline_report)
    monkeypatch.setattr(batch, "now_utc", lambda: late)
    assert make().recover_once(action, driver)["phase"] == "confirmed"
    with dbf() as db:
        assert db.get(ProcurementTask, action["task_id"]).deadline_report == report
        assert db.scalar(select(func.count()).select_from(ProcurementMessage)) == 1
    assert driver.clicks == 1 and driver.lookups == 0


def test_api_gate_default_off_wrong_executor_and_no_register_route(setup, monkeypatch):
    make, driver, action, transport, *_ = setup
    monkeypatch.delenv("PROCUREMENT_DISPATCH_ENABLED")
    with pytest.raises(AgentApiError, match="503"): transport.reserve(action)
    monkeypatch.setenv("PROCUREMENT_DISPATCH_ENABLED", "1")
    transport.executor_id = "other-pc"
    with pytest.raises(AgentApiError, match="403"): transport.reserve(action)
    monkeypatch.delenv("PROCUREMENT_DISPATCH_EXECUTOR_ID")
    with pytest.raises(AgentApiError, match="503"): transport.reserve(action)
    assert transport.api.post("/api/procurement/agent/dispatch/register-account").status_code == 404


def test_machine_token_still_required(setup):
    transport = setup[3]
    del transport.api.app.dependency_overrides[require_agent_token]
    response = transport.api.post("/api/procurement/agent/dispatch/reserve", json={})
    assert response.status_code in {401, 503}


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI only")
def test_real_dpapi_roundtrip_only_synthetic_permit():
    protector = WindowsPermitProtector()
    value = "offline-only-" + str(uuid4())
    encrypted = protector.seal(value)
    assert value.encode() not in encrypted
    assert WindowsPermitProtector().open(encrypted) == value
    with pytest.raises(SendBlocked): protector.open(b"not-dpapi")
