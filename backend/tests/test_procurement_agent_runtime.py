import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from tools.procurement_agent.drivers import MockDriver, ObservedReply, SendResult
from tools.procurement_agent.client import AgentApiError
from tools.procurement_agent import runtime as runtime_module
from tools.procurement_agent.runtime import ProcurementAgent
from tools.procurement_agent.send_journal import SendBlocked, SendJournal


@pytest.fixture()
def journal(tmp_path):
    return SendJournal(tmp_path / "send.sqlite3", server_scope="https://erp.invalid")


class FakeClient:
    def __init__(self):
        self.events = []
        self.heartbeats = []
        self.sent = []
        self.failures = []
        self.manual = []
        self.replies = []
        self.actions = []
        self.discovery_actions = []
        self.candidates = []
        self.discovery_failures = []
        self.conversations = []

    def heartbeat(self, payload):
        self.events.append("heartbeat")
        self.heartbeats.append(payload)
        return {"ok": True}

    def claim(self, payload):
        self.events.append("claim-send")
        return {"ok": True, "actions": list(self.actions)}

    def claim_discovery(self, payload):
        self.events.append("claim-discovery")
        return {"ok": True, "actions": list(self.discovery_actions)}

    def report_candidate(self, inquiry_id, payload):
        self.candidates.append((inquiry_id, payload))
        return {"ok": True, "duplicate": False}

    def report_discovery_failure(self, inquiry_id, payload):
        self.discovery_failures.append((inquiry_id, payload))
        return {"ok": True}

    def confirm_sent(self, inquiry_id, payload):
        self.sent.append((inquiry_id, payload))
        return {"ok": True}

    def report_failure(self, inquiry_id, payload):
        self.failures.append((inquiry_id, payload))
        return {"ok": True}

    def manual_handoff(self, inquiry_id, payload):
        self.manual.append((inquiry_id, payload))
        return {"ok": True}

    def watch(self, capabilities, limit=100):
        self.events.append("watch-replies")
        return {"ok": True, "conversations": list(self.conversations)}

    def report_reply(self, inquiry_id, payload):
        self.replies.append((inquiry_id, payload))
        return {"ok": True}


def _action():
    return {
        "task_id": 1,
        "inquiry_id": 11,
        "action_key": "initial:0",
        "lease_token": "lease-11",
        "required_capability": "taobao_desktop",
        "suggested_message": "您好，请报价",
    }


def _discovery_action():
    return {
        "inquiry_id": 12,
        "slot_no": 2,
        "lease_token": "lease-12",
        "required_capability": "taobao_desktop",
        "search_query": "岩板 厂家 批发",
        "item_name": "岩板",
    }


def test_dry_run_never_calls_driver_or_callbacks():
    client = FakeClient()
    client.actions = [{**_action(), "preview": True, "lease_token": None}]
    driver = MockDriver()
    agent = ProcurementAgent(
        client=client,
        agent_id="agent-1",
        display_name="测试采购机",
        mode="dry_run",
        drivers={"taobao_desktop": driver},
    )

    actions = agent.process_actions_once()

    assert len(actions) == 1
    assert driver.sent == []
    assert client.sent == []
    assert client.failures == []


def test_dry_run_full_cycle_does_not_enter_any_platform_driver():
    class ForbiddenDriver(MockDriver):
        def send(self, *args, **kwargs):
            pytest.fail("dry-run entered send")

        def discover(self, *args, **kwargs):
            pytest.fail("dry-run entered discovery")

        def poll_replies(self, *args, **kwargs):
            pytest.fail("dry-run entered inbox")

    client = FakeClient()
    client.actions = [_action()]
    client.discovery_actions = [_discovery_action()]
    client.conversations = [{"inquiry_id": 11, "required_capability": "taobao_desktop"}]
    agent = ProcurementAgent(
        client=client, agent_id="test", display_name="test", mode="dry_run",
        drivers={"taobao_desktop": ForbiddenDriver()},
    )
    agent.run_once()
    assert not any((client.sent, client.replies, client.candidates, client.failures, client.manual))


@pytest.mark.parametrize("path", [None, "", "relative.sqlite3", str(PROJECT_ROOT / "forbidden.sqlite3")])
def test_cli_rejects_unsafe_journal_before_constructing_erp_client(monkeypatch, path):
    monkeypatch.setenv("PROCUREMENT_AGENT_TOKEN", "synthetic-test-only")
    monkeypatch.setattr(runtime_module, "_load_config", lambda _: {
        "mode": "review", "send_journal_path": path,
        "drivers": {"taobao_desktop": {"command": ["must-not-run"]}},
    })
    def forbidden_client(*args, **kwargs):
        pytest.fail("unsafe configuration reached ERP client")
    monkeypatch.setattr(runtime_module, "ProcurementApiClient", forbidden_client)
    with pytest.raises(SystemExit, match="send_journal_path"):
        runtime_module.main(["--config", "synthetic-config", "--once"])


def test_review_mode_routes_confirmed_send_callback(journal):
    client = FakeClient()
    client.actions = [_action()]
    driver = MockDriver()
    agent = ProcurementAgent(
        client=client,
        agent_id="agent-1",
        display_name="测试采购机",
        mode="review",
        drivers={"taobao_desktop": driver},
        send_journal=journal,
    )

    agent.process_actions_once()

    assert len(driver.sent) == 1
    assert client.sent[0][0] == 11
    assert client.sent[0][1]["external_message_id"] == "mock-out-11"
    assert agent.counters["sent"] == 1
    assert journal.state(_action()) == "confirmed"


def test_review_mode_routes_discovered_candidate_callback(journal):
    client = FakeClient()
    client.discovery_actions = [_discovery_action()]
    driver = MockDriver()
    agent = ProcurementAgent(
        client=client,
        agent_id="agent-1",
        display_name="测试采购机",
        mode="review",
        drivers={"taobao_desktop": driver},
        send_journal=journal,
    )

    agent.process_discoveries_once()

    assert client.candidates[0][0] == 12
    assert client.candidates[0][1]["merchant_external_id"] == "mock-shop-2"
    assert agent.counters["discovered"] == 1


def test_driver_exception_is_unknown_and_never_retryable(journal):
    class BrokenDriver(MockDriver):
        def send(self, action, *, mode):
            raise RuntimeError("窗口暂时未找到")

    client = FakeClient()
    client.actions = [_action()]
    agent = ProcurementAgent(
        client=client,
        agent_id="agent-1",
        display_name="测试采购机",
        mode="review",
        drivers={"taobao_desktop": BrokenDriver()},
        send_journal=journal,
    )

    agent.process_actions_once()

    assert client.failures[0][0] == 11
    assert client.failures[0][1]["retryable"] is False
    assert "禁止自动重发" in client.failures[0][1]["error"]
    assert journal.state(_action()) == "unknown"


def test_poll_replies_routes_by_capability(journal):
    class ReplyDriver(MockDriver):
        def poll_replies(self, conversations):
            assert len(conversations) == 1
            return [
                ObservedReply(
                    inquiry_id=11,
                    external_message_id="reply-11",
                    content="含运 470 元",
                    quote_complete=True,
                    normalized_unit_price=470,
                )
            ]

    client = FakeClient()
    client.conversations = [
        {"inquiry_id": 11, "required_capability": "taobao_desktop"}
    ]
    agent = ProcurementAgent(
        client=client,
        agent_id="agent-1",
        display_name="测试采购机",
        mode="review",
        drivers={"taobao_desktop": ReplyDriver()},
        send_journal=journal,
    )

    count = agent.poll_replies_once()

    assert count == 1
    assert client.replies[0][1]["normalized_unit_price"] == 470
    assert agent.counters["replies"] == 1


def test_run_once_checks_replies_before_discovery_and_send():
    client = FakeClient()
    agent = ProcurementAgent(
        client=client,
        agent_id="agent-1",
        display_name="测试采购机",
        mode="dry_run",
        drivers={},
        declared_capabilities=["taobao_desktop"],
    )

    agent.run_once()

    assert client.events == [
        "heartbeat",
        "watch-replies",
        "claim-discovery",
        "claim-send",
        "heartbeat",
    ]


def test_headless_agent_retries_when_stderr_is_unavailable(monkeypatch):
    client = FakeClient()
    agent = ProcurementAgent(
        client=client,
        agent_id="agent-1",
        display_name="测试采购机",
        mode="dry_run",
        drivers={},
        declared_capabilities=["taobao_desktop"],
    )

    def unavailable():
        raise AgentApiError("ERP temporarily unavailable")

    def stop_after_retry(_seconds):
        raise StopIteration

    monkeypatch.setattr(agent, "run_once", unavailable)
    monkeypatch.setattr(runtime_module.sys, "stderr", None)
    monkeypatch.setattr(runtime_module.time, "sleep", stop_after_retry)

    with pytest.raises(StopIteration):
        agent.run_forever(poll_seconds=10)


def _guarded_agent(client, driver, journal):
    return ProcurementAgent(
        client=client, agent_id="test-agent", display_name="test", mode="review",
        drivers={"taobao_desktop": driver}, send_journal=journal,
    )


def test_review_without_journal_stops_before_claim():
    client, driver = FakeClient(), MockDriver()
    with pytest.raises(SendBlocked):
        _guarded_agent(client, driver, None).process_actions_once()
    assert client.events == []
    assert driver.sent == []


def test_effect_then_exception_is_not_replayed_after_restart(journal):
    class EffectThenCrash(MockDriver):
        def send(self, action, *, mode):
            assert journal.state(action) == "executing"
            self.sent.append(action)
            raise RuntimeError("private driver diagnostic must not be forwarded")

    client, driver = FakeClient(), EffectThenCrash()
    client.actions = [_action()]
    _guarded_agent(client, driver, journal).process_actions_once()
    reopened = SendJournal(journal.path, server_scope="https://erp.invalid")
    client.actions = [{**_action(), "lease_token": "new-lease-after-timeout"}]
    _guarded_agent(client, driver, reopened).process_actions_once()
    assert len(driver.sent) == 1
    assert client.failures[0][1]["retryable"] is False
    assert "private driver" not in client.failures[0][1]["error"]
    assert len(client.manual) == 1


@pytest.mark.parametrize("callback_recorded", [False, True])
def test_confirmation_loss_never_becomes_a_second_send(journal, callback_recorded):
    class LostConfirmation(FakeClient):
        def confirm_sent(self, inquiry_id, payload):
            if callback_recorded:
                super().confirm_sent(inquiry_id, payload)
            raise AgentApiError("response lost")

    client, driver = LostConfirmation(), MockDriver()
    client.actions = [_action()]
    _guarded_agent(client, driver, journal).process_actions_once()
    assert journal.state(_action()) == "sent_observed"
    assert client.failures[0][1]["retryable"] is False
    _guarded_agent(client, driver, journal).process_actions_once()
    assert len(driver.sent) == 1


def test_failure_callback_loss_still_keeps_durable_guard(journal):
    class OfflineClient(FakeClient):
        def report_failure(self, inquiry_id, payload):
            raise AgentApiError("offline")

    class BrokenDriver(MockDriver):
        def send(self, action, *, mode):
            self.sent.append(action)
            raise RuntimeError("driver failed")

    client, driver = OfflineClient(), BrokenDriver()
    client.actions = [_action()]
    with pytest.raises(AgentApiError):
        _guarded_agent(client, driver, journal).process_actions_once()
    assert journal.state(_action()) == "unknown"
    _guarded_agent(client, driver, journal).process_actions_once()
    assert len(driver.sent) == 1


@pytest.mark.parametrize("result", [
    SendResult(outcome="failed", retryable=True),
    SendResult(outcome="unknown"),
    SendResult(outcome="unrecognized"),
    SendResult(outcome="sent", sent_content="您好，请报价"),
    SendResult(outcome="sent", external_message_id="id", sent_content="wrong content"),
    SendResult(outcome="sent", external_message_id="human-review-123", sent_content="您好，请报价"),
])
def test_unproven_delivery_is_never_retryable(journal, result):
    class UnprovenDriver(MockDriver):
        def send(self, action, *, mode):
            self.sent.append(action)
            return result

    client, driver = FakeClient(), UnprovenDriver()
    client.actions = [_action()]
    _guarded_agent(client, driver, journal).process_actions_once()
    assert client.sent == []
    assert client.failures[0][1]["retryable"] is False
    assert journal.state(_action()) == "unknown"


def test_manual_result_stops_batch_and_remains_blocked(journal):
    class ManualDriver(MockDriver):
        def send(self, action, *, mode):
            self.sent.append(action)
            return SendResult(outcome="manual", reason="人工核对")

    client, driver = FakeClient(), ManualDriver()
    client.actions = [_action(), {**_action(), "inquiry_id": 12}]
    _guarded_agent(client, driver, journal).process_actions_once()
    assert len(driver.sent) == 1
    assert journal.state(_action()) == "manual"
    client.actions = [{**_action(), "inquiry_id": 12}]
    _guarded_agent(client, driver, journal).process_actions_once()
    assert len(driver.sent) == 1


def test_storage_failure_happens_before_driver(journal, monkeypatch):
    def full_disk(_action):
        raise OSError("disk full")

    monkeypatch.setattr(journal, "begin", full_disk)
    client, driver = FakeClient(), MockDriver()
    client.actions = [_action()]
    with pytest.raises(OSError):
        _guarded_agent(client, driver, journal).process_actions_once()
    assert driver.sent == []


def test_crash_without_exception_handler_is_still_blocked(journal):
    class HardCrash(MockDriver):
        def send(self, action, *, mode):
            self.sent.append(action)
            raise SystemExit("simulated process exit")

    client, driver = FakeClient(), HardCrash()
    client.actions = [_action()]
    with pytest.raises(SystemExit):
        _guarded_agent(client, driver, journal).process_actions_once()
    assert journal.state(_action()) == "executing"
    reopened = SendJournal(journal.path, server_scope="https://erp.invalid")
    _guarded_agent(client, driver, reopened).process_actions_once()
    assert len(driver.sent) == 1


@pytest.mark.parametrize("method", ["poll_replies_once", "process_discoveries_once"])
def test_unresolved_send_prevents_inbox_or_discovery_switch(journal, method):
    journal.begin(_action())
    client, driver = FakeClient(), MockDriver()
    with pytest.raises(SendBlocked):
        getattr(_guarded_agent(client, driver, journal), method)()
    assert client.events == []
    assert driver.sent == []


@pytest.mark.parametrize("method", ["process_actions_once", "poll_replies_once", "process_discoveries_once"])
def test_other_process_lock_blocks_all_ui_operations(journal, method):
    client, driver = FakeClient(), MockDriver()
    another = SendJournal(journal.path, server_scope="https://erp.invalid")
    with another.execution_lock():
        with pytest.raises(SendBlocked):
            getattr(_guarded_agent(client, driver, journal), method)()
    assert client.events == []


def test_sent_guard_uses_existing_erp_nonretryable_handoff(db_session, journal):
    from app.models.procurement import ProcurementInquiry
    from app.services import procurement_service as service

    task = service.create_task(db_session, {
        "title": "offline safety", "item_name": "fixture", "quantity": 1,
        "unit": "件", "category": "daily", "execution_mode": "agent",
        "channels": ["taobao"], "planned_merchant_count": 1,
        "ab_test_enabled": False, "ab_test_sample_size": 0,
    }, created_by="test")
    service.review_scripts(db_session, task, script_a="synthetic question", script_b=None, reviewed_by="test")
    inquiry = service.prepare_inquiries(db_session, task, [{"merchant_name": "synthetic merchant"}])[0]
    service.review_inquiry_message(db_session, task, inquiry, content="synthetic question", reviewed_by="test")
    db_session.commit()

    class ServiceClient(FakeClient):
        def claim(self, payload):
            actions = service.claim_agent_actions(db_session, **payload)
            db_session.commit()
            return {"actions": actions}

        def report_failure(self, inquiry_id, payload):
            row = service.agent_failure(db_session, inquiry=db_session.get(ProcurementInquiry, inquiry_id), **payload)
            db_session.commit()
            return {"status": row.status}

    class EffectThenFailure(MockDriver):
        def send(self, action, *, mode):
            self.sent.append(action)
            raise RuntimeError("after simulated effect")

    client, driver = ServiceClient(), EffectThenFailure()
    agent = _guarded_agent(client, driver, journal)
    agent.process_actions_once()
    assert inquiry.status == "needs_manual"
    assert task.status == "needs_review"
    assert service.due_actions(db_session, task_id=task.id) == []
    agent.process_actions_once()
    assert len(driver.sent) == 1
