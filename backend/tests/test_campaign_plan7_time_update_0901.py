from datetime import datetime
import json

from sqlalchemy import select

from app.cli import campaign_update_plan7_discount_times as cli
from app.models.campaign import (
    CampaignEvidenceSnapshot,
    CampaignExecutionAttempt,
    CampaignPlan,
)
from app.services import campaign_plan7_time_update_service as svc


def _payload(**updates):
    value = {
        "workflow_key": svc.WORKFLOW_KEY,
        "plan_id": svc.PLAN_ID,
        "activity_ids": list(svc.ACTIVITY_IDS),
        "expected_start_at": svc.EXPECTED_START_AT,
        "expected_end_at": svc.EXPECTED_END_AT,
        "target_start_at": svc.TARGET_START_AT,
        "target_end_at": svc.TARGET_END_AT,
    }
    value.update(updates)
    return value


def _plan(db):
    row = CampaignPlan(
        id=svc.PLAN_ID,
        workflow_key=svc.WORKFLOW_KEY,
        name="plan7",
        campaign_type="super_reduce",
        tier="mid",
        start_at=datetime(2026, 9, 1, 0, 0, 0),
        end_at=datetime(2026, 9, 1, 23, 59, 59),
        status="alarmed",
        platform_activity_mode="long_running_update",
    )
    db.add(row)
    db.commit()
    return row


def _preflight():
    return {
        "ok": True,
        "phase": "preflight",
        "activities": [{
            "activity_id": activity_id,
            "start_at": svc.EXPECTED_START_AT,
            "end_at": svc.EXPECTED_END_AT,
            "sku_level": True,
            "discount_mode": "减钱",
            "editable": True,
        } for activity_id in svc.ACTIVITY_IDS],
        "execution_boundary": {"platform_write": False},
        "web_agent_job_id": "preflight-job",
    }


def _terminal():
    return {
        "ok": True,
        "phase": "commit",
        "submitted": True,
        "confirmed_activity_ids": list(svc.ACTIVITY_IDS),
        "activities": [{
            "activity_id": activity_id,
            "before_start_at": svc.EXPECTED_START_AT,
            "before_end_at": svc.EXPECTED_END_AT,
            "after_start_at": svc.TARGET_START_AT,
            "after_end_at": svc.TARGET_END_AT,
            "platform_terminal": "updated_and_readback_exact",
        } for activity_id in svc.ACTIVITY_IDS],
        "execution_boundary": {
            "platform_read": True,
            "platform_write": True,
            "price_change": False,
            "sku_change": False,
            "scope_change": False,
            "automatic_retry": False,
        },
        "web_agent_job_id": "commit-job",
    }


def test_request_is_exact_and_keeps_times_as_cas_fields():
    assert svc.normalize_request(_payload())["activity_ids"] == list(svc.ACTIVITY_IDS)
    assert len(svc.manifest_sha256(_payload())) == 64
    for changed in (
        {"activity_ids": list(reversed(svc.ACTIVITY_IDS))},
        {"target_end_at": "2026-09-06 00:00:00"},
        {"expected_end_at": svc.TARGET_END_AT},
    ):
        try:
            svc.normalize_request(_payload(**changed))
        except ValueError as exc:
            assert "not_allowed" in str(exc)
        else:
            raise AssertionError("unsafe plan7 time update was accepted")


def test_preflight_claim_commit_readback_is_durable_and_idempotent(
        db_session, monkeypatch):
    plan = _plan(db_session)
    monkeypatch.setattr(svc, "_validate_plan_and_scope", lambda _db: (plan, None))
    calls = []

    def web_call(_db, *, payload, timeout_s=900):
        calls.append(payload["phase"])
        return _preflight() if payload["phase"] == "preflight" else _terminal()

    monkeypatch.setattr(
        svc.web_agent_service, "update_plan7_single_discount_times", web_call)
    first = svc.update_plan7_single_discount_times(
        db_session, request_payload=_payload())
    assert first["ok"] is True
    assert first["attempt_state"] == "completed"
    assert first["execution_boundary"]["platform_write"] is True
    assert calls == ["preflight", "commit"]

    saved = db_session.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.operation == svc.OPERATION
    )).scalar_one()
    assert saved.write_claimed is True
    assert saved.automatic_retry_allowed is False
    assert saved.platform_write_observed is True
    evidence = db_session.execute(select(CampaignEvidenceSnapshot).where(
        CampaignEvidenceSnapshot.evidence_type
        == "plan7_discount_time_update_terminal"
    )).scalar_one()
    assert evidence.result_status == "completed"
    assert len(evidence.rows) == 3

    second = svc.update_plan7_single_discount_times(
        db_session, request_payload=_payload())
    assert second["ok"] is True
    assert second["idempotent_replay"] is True
    assert calls == ["preflight", "commit"]


def test_unknown_after_first_confirm_is_never_retried(db_session, monkeypatch):
    plan = _plan(db_session)
    monkeypatch.setattr(svc, "_validate_plan_and_scope", lambda _db: (plan, None))
    calls = []

    def web_call(_db, *, payload, timeout_s=900):
        calls.append(payload["phase"])
        if payload["phase"] == "preflight":
            return _preflight()
        return {
            "ok": False,
            "error": "browser_transport_lost",
            "step": "commit_unknown",
            "submitted": True,
            "confirmed_activity_ids": [svc.ACTIVITY_IDS[0]],
            "activities": [],
            "execution_boundary": {
                "platform_read": True,
                "platform_write": True,
                "automatic_retry": False,
            },
            "web_agent_job_id": "unknown-job",
        }

    monkeypatch.setattr(
        svc.web_agent_service, "update_plan7_single_discount_times", web_call)
    first = svc.update_plan7_single_discount_times(
        db_session, request_payload=_payload())
    assert first["ok"] is False
    assert first["attempt_state"] == "unknown"
    assert first["automatic_retry"] is False
    second = svc.update_plan7_single_discount_times(
        db_session, request_payload=_payload())
    assert second["ok"] is False
    assert second["error"] == "plan7_discount_time_update_already_claimed"
    assert second["automatic_retry"] is False
    assert calls == ["preflight", "commit"]


def test_preflight_failure_creates_no_write_claim(db_session, monkeypatch):
    plan = _plan(db_session)
    monkeypatch.setattr(svc, "_validate_plan_and_scope", lambda _db: (plan, None))
    monkeypatch.setattr(
        svc.web_agent_service, "update_plan7_single_discount_times",
        lambda *_args, **_kwargs: {
            "ok": False,
            "error": "activity_time_cas_mismatch",
            "execution_boundary": {"platform_write": False},
        })
    result = svc.update_plan7_single_discount_times(
        db_session, request_payload=_payload())
    assert result["ok"] is False
    assert result["safe_retry_before_write"] is True
    assert result["execution_boundary"]["platform_write"] is False
    assert db_session.execute(select(CampaignExecutionAttempt)).scalars().all() == []


def test_cli_preserves_non_2xx_json(monkeypatch, capsys):
    raw = json.dumps(_payload()).encode()
    monkeypatch.setattr(cli, "_read_payload", lambda: raw)
    monkeypatch.setattr(cli, "_service_token", lambda: "token")
    body = b'{"detail":{"error":"activity_time_cas_mismatch"}}'
    monkeypatch.setattr(cli, "call_api", lambda *_args, **_kwargs: (409, body))
    assert cli.main() == 1
    assert "activity_time_cas_mismatch" in capsys.readouterr().out
