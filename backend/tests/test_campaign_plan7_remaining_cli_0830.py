"""The plan-7 operator command must stay visibly alive during long reads."""
from __future__ import annotations

import time

from app.cli import campaign_execute_plan7_remaining as cli
from app.cli import campaign_audit_plan7_partial_signup as audit_cli
from app.cli import campaign_publish_plan7_existing_drafts as publish_cli
from app import dependencies


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
        "plan7-scope-review-08a753484e03")
    assert cli._FIXED_PAYLOAD["expected_item_scope_sha256"] == (
        "1f66d114e711b0fb3448a8a1503120bb5edd35a2d6416105f66545392f15bc86")


def test_partial_audit_payload_is_bound_to_the_single_failed_attempt():
    assert audit_cli._FIXED_PAYLOAD == {
        "workflow_key": "campaign:super-reduce:2026-09-01",
        "plan_id": 7,
        "expected_attempt_id": "782299846f10d86ef4742c20",
        "expected_manifest_sha256": (
            "2fa747d77823ed63baee82c5dbcc0d0fff6e248f77583dd4c9b074fa57d5c30d"
        ),
    }
    assert dependencies.CAMPAIGN_PLAN7_PARTIAL_SIGNUP_AUDIT_PATH in (
        dependencies.CAMPAIGN_PREPARE_SERVICE_PATHS)


def test_draft_publish_payload_is_bound_to_two_audited_existing_drafts():
    assert publish_cli._FIXED_PAYLOAD == {
        "workflow_key": "campaign:super-reduce:2026-09-01",
        "plan_id": 7,
        "expected_attempt_id": "782299846f10d86ef4742c20",
        "expected_snapshot_id": 9,
        "expected_scope_sha256": (
            "0355c293c277330e490858df4f6b4bb57484881fcea9897f27c194b68fb7231b"
        ),
    }
    assert dependencies.CAMPAIGN_PLAN7_DRAFT_PUBLISH_PATH in (
        dependencies.CAMPAIGN_PREPARE_SERVICE_PATHS)
