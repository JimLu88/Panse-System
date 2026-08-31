from datetime import datetime
import json

from sqlalchemy import select

from app.cli import campaign_update_plan7_discount_times as cli
from app.cli import campaign_recover_plan7_discount_times as recovery_cli
from app.cli import campaign_recover_plan7_discount_times_v2 as recovery_v2_cli
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


def _recovery_payload(**updates):
    value = {
        **_payload(),
        "failed_attempt_id": svc.RECOVERY_FAILED_ATTEMPT_ID,
        "prewrite_receipts": [
            {**receipt, "confirmed_activity_ids": list(
                receipt["confirmed_activity_ids"])}
            for receipt in svc.RECOVERY_PREWRITE_RECEIPTS
        ],
    }
    value.update(updates)
    return value


def _recovery_v2_payload(**updates):
    value = {
        **_recovery_payload(),
        "first_recovery_receipt": {
            **svc.RECOVERY_V2_PREWRITE_RECEIPT,
            "confirmed_activity_ids": list(
                svc.RECOVERY_V2_PREWRITE_RECEIPT[
                    "confirmed_activity_ids"]),
        },
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


def _failed_write_free_attempt(db, *, write_observed=False,
                               confirmed_activity_ids=None):
    digest = svc.manifest_sha256(_payload())
    confirmed = confirmed_activity_ids or []
    boundary = {
        "platform_read": True,
        "platform_write": bool(write_observed),
        "account_action": bool(write_observed),
        "automatic_retry": False,
    }
    attempt = CampaignExecutionAttempt(
        id=svc.RECOVERY_FAILED_ATTEMPT_ID,
        plan_id=svc.PLAN_ID,
        workflow_key=svc.WORKFLOW_KEY,
        operation=svc.OPERATION,
        scope_sha256=digest,
        state="failed",
        write_claimed=True,
        write_claimed_at=datetime.now(),
        platform_write_observed=bool(write_observed),
        automatic_retry_allowed=False,
        request_id="plan7-time-update-original",
        web_agent_job_id=svc.RECOVERY_FAILED_WEB_AGENT_JOB_ID,
        last_step="cas_pre_read",
        error_code="activity row zero",
        result_summary={
            "ok": False,
            "phase": "commit",
            "error": (
                "RuntimeError: 活动 143780562424 "
                "在活动列表中不是唯一记录（0条）"),
            "step": "cas_pre_read",
            "submitted": bool(write_observed),
            "confirmed_activity_ids": confirmed,
            "activities": [],
            "web_agent_job_id": svc.RECOVERY_FAILED_WEB_AGENT_JOB_ID,
            "execution_boundary": boundary,
        },
    )
    snapshot = CampaignEvidenceSnapshot(
        plan_id=svc.PLAN_ID,
        workflow_key=svc.WORKFLOW_KEY,
        evidence_type="plan7_discount_time_update_terminal",
        request_id=attempt.request_id,
        web_agent_job_id=svc.RECOVERY_FAILED_WEB_AGENT_JOB_ID,
        scope_sha256=digest,
        result_status="failed",
        platform_summary={"platform_write_observed": bool(write_observed)},
        rows=[],
        failure_rows=[],
        execution_boundary=boundary,
    )
    db.add_all([attempt, snapshot])
    db.commit()
    return attempt


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


def test_recovery_request_is_bound_to_two_exact_write_free_receipts():
    normalized = svc.normalize_recovery_request(_recovery_payload())
    assert normalized["failed_attempt_id"] == svc.RECOVERY_FAILED_ATTEMPT_ID
    bad = _recovery_payload()
    bad["prewrite_receipts"][1]["platform_write"] = True
    try:
        svc.normalize_recovery_request(bad)
    except ValueError as exc:
        assert "receipts_mismatch" in str(exc)
    else:
        raise AssertionError("write-observed recovery receipt was accepted")


def test_v2_recovery_is_bound_to_first_write_free_recovery_receipt():
    normalized = svc.normalize_recovery_v2_request(_recovery_v2_payload())
    assert normalized["first_recovery_receipt"]["request_id"] == "ecee536af3b8"
    bad = _recovery_v2_payload()
    bad["first_recovery_receipt"]["recovery_not_claimed"] = False
    try:
        svc.normalize_recovery_v2_request(bad)
    except ValueError as exc:
        assert "receipt_mismatch" in str(exc)
    else:
        raise AssertionError("unsafe first-recovery receipt was accepted")


def test_v1_recovery_is_permanently_retired(db_session):
    result = svc.recover_plan7_single_discount_times(
        db_session, request_payload=_recovery_payload())
    assert result["ok"] is False
    assert result["error"] == "plan7_discount_time_recovery_v1_retired"
    assert result["recovery_not_claimed"] is True
    assert result["execution_boundary"]["platform_write"] is False


def test_write_free_failed_attempt_can_claim_one_recovery_only(
        db_session, monkeypatch):
    plan = _plan(db_session)
    old = _failed_write_free_attempt(db_session)
    monkeypatch.setattr(svc, "_validate_plan_and_scope", lambda _db: (plan, None))
    calls = []

    def web_call(_db, *, payload, timeout_s=900):
        calls.append(payload["phase"])
        return _preflight() if payload["phase"] == "preflight" else _terminal()

    monkeypatch.setattr(
        svc.web_agent_service, "update_plan7_single_discount_times", web_call)
    result = svc.recover_plan7_single_discount_times_v2(
        db_session, request_payload=_recovery_v2_payload())
    assert result["ok"] is True
    assert result["attempt_state"] == "completed"
    assert result["recovered_from_attempt_id"] == old.id
    assert calls == ["preflight", "commit"]
    db_session.refresh(old)
    assert old.state == "failed"
    assert old.platform_write_observed is False
    attempts = db_session.execute(select(CampaignExecutionAttempt)).scalars().all()
    assert sorted(row.operation for row in attempts) == [
        svc.OPERATION, svc.RECOVERY_V2_OPERATION]

    replay = svc.recover_plan7_single_discount_times_v2(
        db_session, request_payload=_recovery_v2_payload())
    assert replay["ok"] is True
    assert replay["idempotent_replay"] is True
    assert calls == ["preflight", "commit"]


def test_recovery_refuses_any_write_or_confirm_evidence(
        db_session, monkeypatch):
    plan = _plan(db_session)
    _failed_write_free_attempt(
        db_session, write_observed=True,
        confirmed_activity_ids=[svc.ACTIVITY_IDS[0]])
    monkeypatch.setattr(svc, "_validate_plan_and_scope", lambda _db: (plan, None))
    monkeypatch.setattr(
        svc.web_agent_service, "update_plan7_single_discount_times",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unsafe recovery reached Web-Agent")))
    result = svc.recover_plan7_single_discount_times_v2(
        db_session, request_payload=_recovery_v2_payload())
    assert result["ok"] is False
    assert result["error"] == (
        "plan7_discount_time_recovery_write_free_proof_failed")
    assert len(db_session.execute(select(CampaignExecutionAttempt)).scalars().all()) == 1


def test_recovery_requires_current_old_time_before_new_claim(
        db_session, monkeypatch):
    plan = _plan(db_session)
    _failed_write_free_attempt(db_session)
    monkeypatch.setattr(svc, "_validate_plan_and_scope", lambda _db: (plan, None))
    monkeypatch.setattr(
        svc.web_agent_service, "update_plan7_single_discount_times",
        lambda *_args, **_kwargs: {
            "ok": False,
            "error": "current_activity_time_drift",
            "step": "cas_pre_read",
            "submitted": False,
            "confirmed_activity_ids": [],
            "execution_boundary": {"platform_write": False},
        })
    result = svc.recover_plan7_single_discount_times_v2(
        db_session, request_payload=_recovery_v2_payload())
    assert result["ok"] is False
    assert result["recovery_not_claimed"] is True
    assert result["safe_retry_before_write"] is False
    attempts = db_session.execute(select(CampaignExecutionAttempt)).scalars().all()
    assert [row.operation for row in attempts] == [svc.OPERATION]


def test_recovery_cli_preserves_non_2xx_json(monkeypatch, capsys):
    raw = json.dumps(_recovery_payload()).encode()
    monkeypatch.setattr(recovery_cli, "_read_payload", lambda: raw)
    monkeypatch.setattr(recovery_cli, "_service_token", lambda: "token")
    body = b'{"detail":{"error":"recovery_write_free_proof_failed"}}'
    monkeypatch.setattr(
        recovery_cli, "call_api", lambda *_args, **_kwargs: (409, body))
    assert recovery_cli.main() == 1
    assert "recovery_write_free_proof_failed" in capsys.readouterr().out


def test_v2_recovery_refuses_existing_v1_claim(db_session, monkeypatch):
    plan = _plan(db_session)
    _failed_write_free_attempt(db_session)
    digest = svc.manifest_sha256(_payload())
    db_session.add(CampaignExecutionAttempt(
        id="v1claim00000000000000001",
        plan_id=svc.PLAN_ID,
        workflow_key=svc.WORKFLOW_KEY,
        operation=svc.RECOVERY_OPERATION,
        scope_sha256=digest,
        state="failed",
        write_claimed=True,
        automatic_retry_allowed=False,
        request_id="v1-claim",
    ))
    db_session.commit()
    monkeypatch.setattr(svc, "_validate_plan_and_scope", lambda _db: (plan, None))
    result = svc.recover_plan7_single_discount_times_v2(
        db_session, request_payload=_recovery_v2_payload())
    assert result["ok"] is False
    assert result["error"] == "plan7_discount_time_recovery_v1_claim_exists"


def test_v2_recovery_cli_preserves_non_2xx_json(monkeypatch, capsys):
    raw = json.dumps(_recovery_v2_payload()).encode()
    monkeypatch.setattr(recovery_v2_cli, "_read_payload", lambda: raw)
    monkeypatch.setattr(recovery_v2_cli, "_service_token", lambda: "token")
    body = b'{"detail":{"error":"recovery_v1_claim_exists"}}'
    monkeypatch.setattr(
        recovery_v2_cli, "call_api", lambda *_args, **_kwargs: (409, body))
    assert recovery_v2_cli.main() == 1
    assert "recovery_v1_claim_exists" in capsys.readouterr().out
