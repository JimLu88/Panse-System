from datetime import datetime
from decimal import Decimal
import inspect
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api import web_agent as web_agent_api
from app.json_utils import to_jsonable
from app.services import web_agent_service, web_agent_wake_service


def test_campaign_export_allows_large_platform_jobs_to_finish():
    parameter = inspect.signature(
        web_agent_service.campaign_export_items).parameters["timeout_s"]
    assert parameter.default == 720


def test_json_normalizer_keeps_decimal_exact():
    value = {
        "amount": Decimal("123.4500"),
        "at": datetime(2026, 8, 6, 18, 0, 0),
        "path": Path("orders/report.xlsx"),
    }
    assert to_jsonable(value) == {
        "amount": "123.4500",
        "at": "2026-08-06T18:00:00",
        "path": str(Path("orders/report.xlsx")),
    }


def test_wake_command_is_persistent_and_acknowledged(db_session):
    command = web_agent_wake_service.request(
        db_session,
        "start",
        reason="test",
        now=datetime(2026, 8, 6, 17, 59).astimezone(),
    )
    pending = web_agent_wake_service.next_command(
        db_session,
        agent_id="pc-test",
        now=datetime(2026, 8, 6, 18, 0).astimezone(),
    )
    assert pending["id"] == command["id"]
    assert pending["action"] == "start"

    ack = web_agent_wake_service.acknowledge(
        db_session,
        command_id=command["id"],
        agent_id="pc-test",
        status="running",
        detail="started",
        now=datetime(2026, 8, 6, 18, 0, 5).astimezone(),
    )
    assert ack["ok"] is True
    assert web_agent_wake_service.next_command(
        db_session,
        agent_id="pc-test",
        now=datetime(2026, 8, 6, 18, 0, 6).astimezone(),
    ) == {"action": "noop"}


def test_wake_start_only_ensures_agent_online(db_session, monkeypatch):
    calls = []
    monkeypatch.setattr(
        web_agent_api.web_agent_service,
        "ensure_online",
        lambda db, **kwargs: calls.append(kwargs) or {"online": True},
    )

    assert web_agent_api.wake_start(db_session) == {
        "online": True,
        "agent": "web-agent",
    }
    assert calls == [{"reason": "review_status_sync"}]


def test_wake_start_reports_bridge_failure(db_session, monkeypatch):
    monkeypatch.setattr(
        web_agent_api.web_agent_service,
        "ensure_online",
        lambda db, **kwargs: {"online": False, "error": "bridge offline"},
    )

    with pytest.raises(HTTPException) as exc:
        web_agent_api.wake_start(db_session)
    assert exc.value.status_code == 409
    assert "bridge offline" in str(exc.value.detail)


def test_ensure_online_uses_long_cold_start_window(db_session, monkeypatch):
    calls = []
    monkeypatch.setattr(
        web_agent_service,
        "_get_raw",
        lambda db, path, timeout=5: calls.append(path) or {"ok": True, "tasks": []},
    )

    result = web_agent_service.ensure_online(db_session, reason="scheduled_order_pull")

    assert result["online"] is True
    assert calls == ["/api/tasks"]


def test_ensure_online_returns_bridge_failure_reason(db_session, monkeypatch):
    command = {"id": "wake-1"}
    monkeypatch.setattr(
        web_agent_service,
        "_get_raw",
        lambda db, path, timeout=5: {"ok": False, "error": "ConnectTimeout"},
    )
    monkeypatch.setattr(
        web_agent_service,
        "request_start",
        lambda db, **kwargs: command,
    )
    monkeypatch.setattr(
        web_agent_wake_service,
        "status",
        lambda db: {
            "command": {
                "id": "wake-1",
                "status": "failed",
                "detail": "Web-Agent exited during startup (code=1)",
            }
        },
    )
    monkeypatch.setattr(web_agent_service.time, "sleep", lambda _seconds: None)

    result = web_agent_service.ensure_online(
        db_session, reason="scheduled_order_pull", wait_s=5
    )

    assert result["online"] is False
    assert result["wake_command_id"] == "wake-1"
    assert result["error"] == "Web-Agent exited during startup (code=1)"


def test_campaign_discovery_wakes_before_posting(db_session, monkeypatch):
    calls = []
    monkeypatch.setattr(
        web_agent_service,
        "ensure_online",
        lambda db, **kwargs: calls.append(("wake", kwargs)) or {"online": True},
    )
    monkeypatch.setattr(
        web_agent_service,
        "_post",
        lambda db, path, payload, **kwargs: calls.append(
            ("post", path, kwargs)
        ) or {"ok": False, "error": "stop-before-wait"},
    )

    result = web_agent_service.campaign_discover(db_session)

    assert result == {"ok": False, "error": "stop-before-wait"}
    assert calls == [
        ("wake", {"reason": "campaign_discovery", "wait_s": 120}),
        ("post", "/api/campaign/discover", {"timeout": 30, "auto_wake": False}),
    ]


def test_campaign_discovery_stops_before_post_when_wake_fails(db_session, monkeypatch):
    monkeypatch.setattr(
        web_agent_service,
        "ensure_online",
        lambda db, **kwargs: {"online": False, "error": "bridge did not answer"},
    )
    monkeypatch.setattr(
        web_agent_service,
        "_post",
        lambda *args, **kwargs: pytest.fail("offline discovery must not be posted"),
    )

    result = web_agent_service.campaign_discover(db_session)

    assert result["ok"] is False
    assert result["error"] == "bridge did not answer"
