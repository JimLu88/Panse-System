"""Plan-8 signup-only recovery: exact failure, scope, evidence and no retry."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from app import dependencies
from app.models.campaign import CampaignExecutionAttempt, CampaignPlan
from app.services import (
    campaign_execution_service,
    campaign_plan8_signup_recovery_service as recovery,
    campaign_policy_service,
    campaign_service,
)


def _plan() -> CampaignPlan:
    return CampaignPlan(
        id=8,
        workflow_key=recovery.WORKFLOW_KEY,
        name="超级88现货",
        campaign_type="big88",
        tier="big",
        start_at=datetime(2026, 9, 6, 20, 0, 0),
        end_at=datetime(2026, 9, 13, 23, 59, 59),
        qn_campaign_title="26年淘宝9月超级88",
        status="alarmed",
        remark="official_all_store=true; official_exempt_items=805268708396",
        platform_activity_mode="fixed_window",
        platform_campaign_id="49462",
        platform_united_activity_id="49469",
        platform_sign_record_id="3527841611",
    )


def _seed_original(db) -> None:
    db.add(CampaignExecutionAttempt(
        id=recovery.ORIGINAL_ATTEMPT_ID,
        plan_id=8,
        workflow_key=recovery.WORKFLOW_KEY,
        operation=recovery.ORIGINAL_OPERATION,
        scope_sha256=recovery.ORIGINAL_OUTER_SCOPE_SHA256,
        state="failed_no_retry",
        write_claimed=True,
        platform_write_observed=True,
        automatic_retry_allowed=False,
        result_summary={
            "discount": {"ok": True, "submitted": True, "job": "job3"},
            "signup": None,
        },
    ))
    db.commit()


def _signup_rows() -> list[dict]:
    counts = {
        "1036279566778": 9,
        "1036312802226": 9,
        "1074244132390": 9,
        "837902729785": 9,
        "841201084787": 8,
        "917179577721": 8,
        "805268708396": 1,
    }
    rows = []
    sku = 6100000000000
    for item_id, count in counts.items():
        for _ in range(count):
            sku += 1
            rows.append({
                "taobao_item_id": item_id,
                "taobao_sku_id": str(sku),
                "sku_code": f"SKU-{sku}",
                "price": 1000.0,
                "is_placeholder": False,
            })
    return rows


def _current(*, candidate_sha: str | None = None) -> dict:
    active = sorted(recovery.EXPECTED_ALREADY_PUBLISHED_ITEM_IDS)
    marketing = []
    for item_id in sorted(recovery.EXPECTED_OFFICIAL_RECORD_ITEM_IDS):
        selected = item_id in recovery.EXPECTED_ALREADY_PUBLISHED_ITEM_IDS
        marketing.append({
            "item_id": item_id,
            "marketing_id": f"m-{item_id}",
            "status": "已发布设定" if selected else "草稿",
            "classification": "enrolled_scheduled" if selected else "unknown",
            "selected": selected,
            "proves_enrollment": selected,
            "proves_active": False,
            "proves_scheduled": selected,
            "sku_count": 1,
        })
    sha = candidate_sha or recovery.EXPECTED_CANDIDATE_SHA256
    return {
        "ok": True,
        "rows": [{"item_id": item_id, "sku_id": f"s-{item_id}",
                  "status": "已发布设定", "activity_price": 1000.0}
                 for item_id in active],
        "floor_refresh": {"recorded": 179},
        "candidate_evidence": {
            "sha256": sha,
            "job_id": "job2",
            "requested_sku_count": 179,
            "observed_sku_count": 171,
            "missing_sku_ids": [str(6200000000000 + i) for i in range(8)],
            "candidate_items_scanned": 50,
            "page_count": 6,
        },
        "candidate_unavailable": {
            "items": ["793202812082"],
            "partial_missing_items": [],
            "complete": True,
            "sha256": sha,
        },
        "export_evidence": {
            "filename": "official.xlsx",
            "size": 14995,
            "sha256": "7" * 64,
            "job_id": "job1",
            "identity": {
                "ok": True,
                "campaign_title": "26年淘宝9月超级88",
                "campaign_id": "49462",
                "united_activity_id": "49469",
                "sign_record_id": "3527841611",
                "campaign_start": "2026-09-06 20:00:00",
                "campaign_end": "2026-09-13 23:59:59",
                "official_rate": "12%",
                "platform_activity_mode": "fixed_window",
            },
            "marketing_records": marketing,
        },
    }


def _request() -> dict:
    return {
        "workflow_key": recovery.WORKFLOW_KEY,
        "expected_plan_id": 8,
        "expected_status": "alarmed",
        "expected_original_attempt_id": recovery.ORIGINAL_ATTEMPT_ID,
        "expected_original_scope_sha256": recovery.ORIGINAL_OUTER_SCOPE_SHA256,
        "expected_full_signup_scope_sha256": recovery.EXPECTED_FULL_SIGNUP_SCOPE_SHA256,
        "expected_pending_scope_sha256": recovery.EXPECTED_PENDING_SCOPE_SHA256,
        "expected_policy_sha256": recovery.EXPECTED_POLICY_SHA256,
        "expected_candidate_sha256": recovery.EXPECTED_CANDIDATE_SHA256,
    }


def _patch_read_gates(monkeypatch, *, current: dict | None = None):
    rows = _signup_rows()
    monkeypatch.setattr(
        campaign_policy_service, "require_policy",
        lambda: {"version": "test", "_sha256": recovery.EXPECTED_POLICY_SHA256})
    monkeypatch.setattr(
        campaign_service, "candidate_unavailable_items_for_plan",
        lambda *_a, **_k: {
            "793202812082": {
                "evidence_sha256": recovery.EXPECTED_CANDIDATE_SHA256,
            },
        })
    monkeypatch.setattr(
        campaign_service, "build_signup_rows",
        lambda *_a, **_k: (list(rows), {"excluded_whole_items": []}))
    monkeypatch.setattr(
        campaign_execution_service, "scope_sha256",
        lambda *, identity, rows, policy_sha256: (
            recovery.EXPECTED_FULL_SIGNUP_SCOPE_SHA256
            if len(rows) == recovery.EXPECTED_FULL_ROW_COUNT
            else recovery.EXPECTED_PENDING_SCOPE_SHA256
        ))
    monkeypatch.setattr(
        campaign_service, "refresh_floor_evidence_from_current_activity",
        lambda *_a, **_k: current or _current())
    monkeypatch.setattr(
        campaign_service, "preflight", lambda *_a, **_k: [
            {"rule": "R16", "level": "pass"},
            {"rule": "R17", "level": "pass", "checked": 52},
        ])
    monkeypatch.setattr(
        campaign_service, "_refresh_official_product_sku_identity",
        lambda *_a, **_k: {
            "ok": True, "checked_items": 6, "checked_skus": 52,
            "ledger_refresh": {"conflicts": []}, "ledger_gate": {"ok": True},
        })


def test_recovery_route_has_narrow_machine_identity():
    assert dependencies.CAMPAIGN_PLAN8_SIGNUP_RECOVERY_PATH == (
        "/api/campaigns/recover-super88-plan8-signup")
    assert (dependencies.CAMPAIGN_PLAN8_SIGNUP_RECOVERY_PATH
            in dependencies.CAMPAIGN_PREPARE_SERVICE_PATHS)


def test_plan8_signup_recovery_runs_once_and_replay_never_refreshes_or_writes(
        db_session, monkeypatch):
    plan = _plan()
    db_session.add(plan)
    db_session.commit()
    _seed_original(db_session)
    _patch_read_gates(monkeypatch)
    calls = []

    original_refresh = campaign_service.refresh_floor_evidence_from_current_activity

    def counted_refresh(*args, **kwargs):
        calls.append("refresh")
        return original_refresh(*args, **kwargs)

    monkeypatch.setattr(
        campaign_service, "refresh_floor_evidence_from_current_activity",
        counted_refresh)

    def fake_push(db, current_plan, **kwargs):
        calls.append("push_signup")
        assert current_plan.status == "resume_executing"
        assert kwargs["execution_source"] == (
            "campaign_super88_plan8_signup_recovery")
        assert kwargs["exact_item_scope"] == recovery.EXPECTED_PENDING_ITEM_IDS
        assert kwargs["reuse_fresh_plan_evidence"] is True
        assert kwargs["prepared_current_activity"]["export_evidence"]["job_id"] == "job1"
        current_plan.status = "signup_pushed"
        db.commit()
        return {
            "ok": True,
            "submitted": True,
            "job": "signup-job",
            "stats": {"execution_attempt_id": "inner-signup-attempt"},
            "terminal_classification": {
                "accepted_item_ids": sorted(recovery.EXPECTED_PENDING_ITEM_IDS),
                "no_sales_item_ids": [],
                "hard_failed_item_ids": [],
            },
            "post_submit_export_evidence": {"sha256": "8" * 64},
            "post_submit_verification": {"ok": True, "failures": []},
        }

    monkeypatch.setattr(campaign_service, "push_signup", fake_push)

    first = recovery.recover_plan8_signup(db_session, **_request())
    replay = recovery.recover_plan8_signup(db_session, **_request())

    assert first["ok"] is True
    assert first["signup_attempt_id"] == "inner-signup-attempt"
    assert first["scope_sha256"] == recovery.EXPECTED_PENDING_SCOPE_SHA256
    assert replay["ok"] is True and replay["idempotent_replay"] is True
    assert calls == ["refresh", "push_signup"]
    attempt = db_session.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.operation == recovery.OPERATION,
    )).scalar_one()
    assert attempt.state == "completed"
    assert attempt.write_claimed is True
    assert attempt.platform_write_observed is True
    assert attempt.automatic_retry_allowed is False


def test_candidate_or_official_export_drift_stops_before_recovery_claim(
        db_session, monkeypatch):
    db_session.add(_plan())
    db_session.commit()
    _seed_original(db_session)
    _patch_read_gates(monkeypatch, current=_current(candidate_sha="9" * 64))
    monkeypatch.setattr(
        campaign_service, "push_signup",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("must stop before signup")))

    result = recovery.recover_plan8_signup(db_session, **_request())

    assert result["ok"] is False
    assert result["error"] == "plan8_signup_recovery_current_activity_mismatch"
    assert db_session.get(CampaignPlan, 8).status == "alarmed"
    assert db_session.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.operation == recovery.OPERATION,
    )).scalar_one_or_none() is None


def test_push_signup_plan8_recovery_source_rejects_unprepared_context(
        db_session, monkeypatch):
    plan = _plan()
    plan.status = "resume_executing"
    db_session.add(plan)
    db_session.commit()
    monkeypatch.setattr(
        campaign_policy_service, "require_policy",
        lambda: {"version": "test", "_sha256": recovery.EXPECTED_POLICY_SHA256})
    monkeypatch.setattr(
        campaign_service, "refresh_floor_evidence_from_current_activity",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("recovery source must not launch a second refresh")))

    result = campaign_service.push_signup(
        db_session,
        plan,
        execution_source="campaign_super88_plan8_signup_recovery",
        reuse_fresh_plan_evidence=True,
        exact_item_scope=recovery.EXPECTED_PENDING_ITEM_IDS,
        prepared_current_activity={},
        prepared_official_product_identity={"ok": False},
    )

    assert result["ok"] is False
    assert result["step"] == "plan8_signup_recovery_policy_guard"
    assert plan.status == "resume_executing"
