from datetime import datetime
from decimal import Decimal
from pathlib import Path

from app.json_utils import to_jsonable
from app.services import web_agent_wake_service


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
