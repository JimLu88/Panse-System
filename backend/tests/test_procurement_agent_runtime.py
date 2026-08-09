import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from tools.procurement_agent.drivers import MockDriver, ObservedReply
from tools.procurement_agent.client import AgentApiError
from tools.procurement_agent import runtime as runtime_module
from tools.procurement_agent.runtime import ProcurementAgent


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
        "inquiry_id": 11,
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


def test_review_mode_routes_confirmed_send_callback():
    client = FakeClient()
    client.actions = [_action()]
    driver = MockDriver()
    agent = ProcurementAgent(
        client=client,
        agent_id="agent-1",
        display_name="测试采购机",
        mode="review",
        drivers={"taobao_desktop": driver},
    )

    agent.process_actions_once()

    assert len(driver.sent) == 1
    assert client.sent[0][0] == 11
    assert client.sent[0][1]["external_message_id"] == "mock-out-11"
    assert agent.counters["sent"] == 1


def test_review_mode_routes_discovered_candidate_callback():
    client = FakeClient()
    client.discovery_actions = [_discovery_action()]
    driver = MockDriver()
    agent = ProcurementAgent(
        client=client,
        agent_id="agent-1",
        display_name="测试采购机",
        mode="review",
        drivers={"taobao_desktop": driver},
    )

    agent.process_discoveries_once()

    assert client.candidates[0][0] == 12
    assert client.candidates[0][1]["merchant_external_id"] == "mock-shop-2"
    assert agent.counters["discovered"] == 1


def test_driver_exception_reports_retryable_failure():
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
    )

    agent.process_actions_once()

    assert client.failures[0][0] == 11
    assert client.failures[0][1]["retryable"] is True
    assert "窗口暂时未找到" in client.failures[0][1]["error"]


def test_poll_replies_routes_by_capability():
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
