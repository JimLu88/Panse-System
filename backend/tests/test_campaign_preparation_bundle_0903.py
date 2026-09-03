from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from types import SimpleNamespace

import pytest

from app.models.campaign import (
    CampaignExecutionAttempt,
    CampaignPlan,
    CampaignPreparationBundle,
)
from app.services import campaign_preparation_service as service
from app.services import settings_service
from app import dependencies
from app.cli import campaign_prepare_final_bundle as bundle_cli


def _plan(db, *, status="draft"):
    plan = CampaignPlan(
        name="2026年9月自动化准备包测试",
        campaign_type="big88",
        tier="big",
        start_at=datetime(2026, 9, 6, 20, 0, 0),
        end_at=datetime(2026, 9, 13, 23, 59, 59),
        qn_campaign_title="26年淘宝9月超级88",
        status=status,
        workflow_key="campaign:test:preparation-bundle",
        platform_campaign_id="49462",
        platform_united_activity_id="49469",
        platform_sign_record_id="3527841611",
        remark="official_all_store=true; official_exempt_items=",
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def _install_common(monkeypatch, *, signup_rows=None, signup_stats=None,
                    discount_rows=None, discount_stats=None, checks=None):
    inventory = [
        {
            "taobao_item_id": "793202812082",
            "taobao_sku_id": "6241447059625",
            "sku_code": "PPS-REAL-1",
            "product_code": "PPS-REAL",
            "daily_price": "100.00",
            "small_promo": "90.00",
            "mid_promo": "85.00",
            "big_promo": "80.00",
            "is_custom_placeholder": False,
            "mapping_updated_at": "2026-09-03T00:00:00+00:00",
            "pricing_updated_at": "2026-09-03T00:00:00+00:00",
        },
        {
            "taobao_item_id": "805268708396",
            "taobao_sku_id": "999900001",
            "sku_code": "PPS-REAL-2",
            "product_code": "PPS-REAL-2",
            "daily_price": "200.00",
            "small_promo": "180.00",
            "mid_promo": "170.00",
            "big_promo": "160.00",
            "is_custom_placeholder": False,
            "mapping_updated_at": "2026-09-03T00:00:00+00:00",
            "pricing_updated_at": "2026-09-03T00:00:00+00:00",
        },
    ]
    signup_rows = signup_rows if signup_rows is not None else [
        {
            "taobao_item_id": "793202812082",
            "taobao_sku_id": "6241447059625",
            "price": 100.0,
            "is_placeholder": False,
            "shipping_days": "30",
        },
        {
            "taobao_item_id": "805268708396",
            "taobao_sku_id": "999900001",
            "price": 200.0,
            "is_placeholder": False,
            "shipping_days": "30",
        },
    ]
    discount_rows = discount_rows if discount_rows is not None else [
        {"taobao_item_id": row["taobao_item_id"],
         "taobao_sku_id": row["taobao_sku_id"], "target_price": 80.0}
        for row in signup_rows
    ]
    monkeypatch.setattr(service, "_inventory_snapshot", lambda db, plan: (
        inventory, {row["taobao_sku_id"]: row for row in inventory}))
    monkeypatch.setattr(service, "_latest_evidence", lambda db, plan: [{
        "id": 7, "type": "candidate_prices", "request_id": "req-1",
        "result_status": "success", "scope_sha256": "e" * 64,
        "artifact_sha256": "f" * 64,
        "created_at": "2026-09-03T00:00:00+00:00",
    }])
    monkeypatch.setattr(service, "_identity", lambda plan: {
        "ok": True, "campaign_title": plan.qn_campaign_title,
        "campaign_id": plan.platform_campaign_id,
        "united_activity_id": plan.platform_united_activity_id,
        "sign_record_id": plan.platform_sign_record_id,
    })
    monkeypatch.setattr(service.campaign_policy_service, "require_policy", lambda: {
        "_sha256": "p" * 64,
    })
    monkeypatch.setattr(
        service.campaign_policy_service, "floor_evidence_max_age_hours", lambda: 24)
    monkeypatch.setattr(
        service.campaign_service, "build_signup_rows",
        lambda db, plan: (signup_rows, signup_stats or {
            "advisory_prior_no_sales_items": ["793202812082"]}))
    monkeypatch.setattr(
        service.campaign_service, "build_discount_rows",
        lambda db, plan: (discount_rows, discount_stats or {}))
    monkeypatch.setattr(
        service.campaign_service, "preflight",
        lambda db, plan, exact_item_scope=None: checks or [{
            "rule": "R17", "title": "价格证据完整", "level": "pass", "items": []}])


def test_ready_bundle_is_immutable_idempotent_and_never_claims_write(db_session, monkeypatch):
    plan = _plan(db_session)
    _install_common(monkeypatch)

    first = service.compile_bundle(
        db_session, workflow_key=plan.workflow_key, expected_plan_id=plan.id)
    second = service.compile_bundle(
        db_session, workflow_key=plan.workflow_key, expected_plan_id=plan.id)

    assert first["ok"] is True
    assert first["ready_for_final_submission"] is True
    assert first["created"] is True
    assert first["prepared_by"] == "system:campaign-preparation-compiler"
    assert second["bundle_id"] == first["bundle_id"]
    assert second["reused"] is True
    assert first["summary"]["prior_no_sales_advisory_count"] == 1
    assert first["summary"]["compiler_schema_version"] == (
        service.COMPILER_SCHEMA_VERSION)
    assert {row["taobao_item_id"] for row in first["signup_rows"]} == {
        "793202812082", "805268708396"}
    assert first["execution_boundary"] == {
        "platform_read": False,
        "platform_write": False,
        "account_action": False,
        "price_change": False,
        "sku_rotation": False,
        "notification": False,
        "automatic_retry": False,
        "write_claim_created": False,
        "allowed_next_step": "campaign_program_final_once",
    }
    assert db_session.query(CampaignExecutionAttempt).count() == 0
    assert db_session.query(CampaignPreparationBundle).count() == 1


def test_bad_real_sku_price_defers_whole_item_but_keeps_other_item(
        db_session, monkeypatch):
    plan = _plan(db_session)
    rows = [
        {"taobao_item_id": "793202812082", "taobao_sku_id": "6241447059625",
         "price": 99.0, "is_placeholder": False},
        {"taobao_item_id": "805268708396", "taobao_sku_id": "999900001",
         "price": 200.0, "is_placeholder": False},
    ]
    _install_common(monkeypatch, signup_rows=rows)

    result = service.compile_bundle(
        db_session, workflow_key=plan.workflow_key, expected_plan_id=plan.id)

    assert result["ready_for_final_submission"] is True
    assert [row["taobao_item_id"] for row in result["signup_rows"]] == [
        "805268708396"]
    decisions = {row["taobao_item_id"]: row for row in result["item_decisions"]}
    assert decisions["793202812082"]["state"] == "deferred_whole_item"
    assert decisions["805268708396"]["state"] == "ready"
    assert decisions["793202812082"]["reasons"][0]["code"] == (
        "real_sku_signup_price_not_erp_daily")


def test_discount_only_item_failure_is_isolated_and_string_item_gate_is_scoped(
        db_session, monkeypatch):
    plan = _plan(db_session)
    _install_common(
        monkeypatch,
        discount_stats={
            "excluded_price_hold_items": [{
                "taobao_item_id": "793202812082",
                "skus": [{
                    "taobao_sku_id": "6241447059625",
                    "record_id": "10031117357515",
                    "reasons": [{"amount": "0.90"}],
                }],
            }],
            "excluded_whole_items": [{
                "taobao_item_id": "1001358847694",
                "reason": "explicit item-level exclusion without mapped SKUs",
            }],
        },
        checks=[{
            "rule": "R17", "title": "一件商品价格证据缺失",
            "level": "error", "items": ["793202812082"],
        }],
    )

    result = service.compile_bundle(
        db_session, workflow_key=plan.workflow_key, expected_plan_id=plan.id)

    assert result["ready_for_final_submission"] is True
    assert {row["taobao_item_id"] for row in result["signup_rows"]} == {
        "805268708396"}
    assert result["summary"]["global_blockers"] == []
    assert result["summary"]["total_item_count"] == 3
    assert result["summary"]["mapped_item_count"] == 2
    assert result["summary"]["ready_item_count"] == 1
    assert result["summary"]["deferred_item_count"] == 1
    assert result["summary"]["excluded_item_count"] == 1
    assert len(result["item_decisions"]) == 3
    assert sum(result["summary"][key] for key in (
        "ready_item_count", "deferred_item_count", "excluded_item_count"
    )) == result["summary"]["total_item_count"]
    decisions = {row["taobao_item_id"]: row for row in result["item_decisions"]}
    assert decisions["793202812082"]["state"] == "deferred_whole_item"
    assert decisions["1001358847694"]["state"] == (
        "excluded_by_explicit_policy")
    assert "6241447059625" not in decisions
    assert "10031117357515" not in decisions


def test_global_gate_blocks_all_and_claimed_unknown_attempt_stays_fail_closed(
        db_session, monkeypatch):
    plan = _plan(db_session)
    _install_common(monkeypatch, checks=[{
        "rule": "R15", "title": "活动身份不完整", "level": "error", "items": []}])
    db_session.add(CampaignExecutionAttempt(
        id="attempt-unknown", plan_id=plan.id, workflow_key=plan.workflow_key,
        operation="signup", scope_sha256="s" * 64, state="claimed",
        write_claimed=True, automatic_retry_allowed=False,
    ))
    db_session.commit()

    result = service.compile_bundle(
        db_session, workflow_key=plan.workflow_key, expected_plan_id=plan.id)

    assert result["ready_for_final_submission"] is False
    codes = {row["code"] for row in result["summary"]["global_blockers"]}
    assert "global_preflight_gate" in codes
    assert "existing_claimed_attempt_requires_readback_or_scoped_recovery" in codes
    assert result["execution_boundary"]["automatic_retry"] is False


def test_latest_ready_bundle_expires_without_becoming_executable(
        db_session, monkeypatch):
    plan = _plan(db_session)
    _install_common(monkeypatch)
    created = service.compile_bundle(
        db_session, workflow_key=plan.workflow_key, expected_plan_id=plan.id)
    row = db_session.get(CampaignPreparationBundle, created["bundle_id"])
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db_session.commit()

    latest = service.get_latest_bundle(
        db_session, workflow_key=plan.workflow_key, expected_plan_id=plan.id)

    assert latest["state"] == "expired_requires_readonly_refresh"
    assert latest["ready_for_final_submission"] is False


def test_bundle_machine_identity_is_encrypted_and_single_path_only(db_session):
    bundle_token = "bundle-token-0903"
    legacy_token = "legacy-campaign-token-0903"
    settings_service.set_value(
        db_session,
        dependencies.CAMPAIGN_PREPARATION_BUNDLE_SERVICE_SETTING,
        bundle_token,
    )
    settings_service.set_value(
        db_session, dependencies.CAMPAIGN_PREPARE_SERVICE_SETTING, legacy_token)
    db_session.commit()

    assert dependencies.machine_identity_for_key(
        bundle_token, db_session,
        path=dependencies.CAMPAIGN_PREPARE_FINAL_BUNDLE_PATH,
    ) == "service:campaign-preparation-bundle"
    for forbidden_path in (
        dependencies.CAMPAIGN_PREPARE_PATH,
        dependencies.CAMPAIGN_EVIDENCE_REFRESH_PATH,
        dependencies.CAMPAIGN_PLAN8_FINAL_RECOVERY_V6_PATH,
        "/api/campaigns/1/push-signup",
    ):
        assert dependencies.machine_identity_for_key(
            bundle_token, db_session, path=forbidden_path) is None
    assert dependencies.machine_identity_for_key(
        legacy_token, db_session,
        path=dependencies.CAMPAIGN_PREPARE_FINAL_BUNDLE_PATH,
    ) is None


def test_refresh_and_compile_calls_only_existing_readonly_refresh(
        db_session, monkeypatch):
    plan = _plan(db_session)
    _install_common(monkeypatch)
    calls = []
    monkeypatch.setattr(
        service.campaign_workflow_service, "refresh_evidence_and_prepare",
        lambda db, workflow_key, expected_plan_id: calls.append(
            (workflow_key, expected_plan_id)) or {"ok": True})

    result = service.compile_bundle(
        db_session, workflow_key=plan.workflow_key,
        expected_plan_id=plan.id, refresh_evidence=True)

    assert calls == [(plan.workflow_key, plan.id)]
    assert result["execution_boundary"]["platform_read"] is True
    assert result["execution_boundary"]["platform_write"] is False
    assert result["execution_boundary"]["write_claim_created"] is False


def test_bundle_cli_rejects_any_field_that_could_request_a_write(monkeypatch):
    raw = json.dumps({
        "workflow_key": "campaign:test:preparation-bundle",
        "plan_id": 8,
        "mode": "compile",
        "submit": True,
    }).encode("utf-8")
    monkeypatch.setattr(
        bundle_cli.sys, "stdin", SimpleNamespace(buffer=BytesIO(raw)))

    with pytest.raises(ValueError, match="不允许字段"):
        bundle_cli._read_payload()
