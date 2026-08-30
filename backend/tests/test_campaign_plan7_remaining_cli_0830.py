"""The plan-7 operator command must stay visibly alive during long reads."""
from __future__ import annotations

import time

from app.cli import campaign_execute_plan7_remaining as cli


def test_long_server_call_emits_heartbeat(monkeypatch, capsys):
    def slow_call(payload: bytes, *, token: str):
        assert payload == b"{}"
        assert token == "service-token"
        time.sleep(0.03)
        return 409, b'{"detail":{"error":"read_failed"}}'

    monkeypatch.setattr(cli, "call_execute", slow_call)

    status, body = cli._call_with_heartbeat(
        b"{}", token="service-token", heartbeat_s=0.005)

    assert status == 409
    assert b"read_failed" in body
    assert "服务器仍在执行只读导出/安全校验" in capsys.readouterr().err


def test_recovery_payload_is_bound_to_the_observed_preclaim_incident():
    assert cli._FIXED_PAYLOAD["recovery_incident_id"] == (
        "plan7-preclaim-export-e222849772c5")
