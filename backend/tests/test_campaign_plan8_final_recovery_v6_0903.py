import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from app import dependencies
from app.cli import campaign_recover_plan8_final_v6 as cli
from app.models.campaign import CampaignExecutionAttempt, CampaignPlan
from app.services import (
    campaign_execution_service,
    campaign_plan8_final_recovery_v6_service as recovery,
    campaign_policy_service,
    campaign_service,
    web_agent_service,
)
from app.services import campaign_plan8_final_recovery_v2_service as recovery_v2


def _plan():
    return CampaignPlan(
        id=8, workflow_key=recovery.WORKFLOW_KEY, name="超级88现货",
        campaign_type="big88", tier="big",
        start_at=datetime(2026, 9, 6, 20, 0, 0),
        end_at=datetime(2026, 9, 13, 23, 59, 59),
        qn_campaign_title="26年淘宝9月超级88", status="alarmed",
        remark="official_all_store=true; official_exempt_items=805268708396",
        platform_activity_mode="fixed_window", platform_campaign_id="49462",
        platform_united_activity_id="49469",
        platform_sign_record_id="3527841611",
    )


def _seed_prerequisites(db):
    for attempt_id, expected in recovery.PREREQUISITE_ATTEMPTS.items():
        operation, state, claimed = expected[:3]
        platform_write = expected[3] if len(expected) == 4 else claimed
        db.add(CampaignExecutionAttempt(
            id=attempt_id, plan_id=8, workflow_key=recovery.WORKFLOW_KEY,
            operation=operation, scope_sha256=(attempt_id * 3)[:64],
            state=state, write_claimed=claimed,
            platform_write_observed=platform_write,
            automatic_retry_allowed=False,
        ))
    db.commit()


def _signup_rows():
    counts = {item_id: spec["final_sku_count"]
              for item_id, spec in recovery.DRAFT_RECORDS.items()}
    rows = []
    fallback = 7100000000000
    custom_left = recovery.EXPECTED_TARGET_CUSTOM_ROW_COUNT
    for item_id, count in counts.items():
        fixed = list(recovery.DRAFT_RECORDS[item_id]["add_sku_ids"])
        sku_ids = list(fixed)
        while len(sku_ids) < count:
            fallback += 1
            sku_ids.append(str(fallback))
        for sku_id in sku_ids:
            custom = custom_left > 0 and sku_id not in recovery.ADD_SKU_IDS
            custom_left -= int(custom)
            rows.append({
                "taobao_item_id": item_id, "taobao_sku_id": sku_id,
                "sku_code": f"SKU-{sku_id}", "price": 1000.0,
                "is_placeholder": custom,
            })
    return rows


def _discount_rows():
    return [{
        "taobao_item_id": item_id, "taobao_sku_id": sku_id,
        "sku_code": f"SKU-{sku_id}", "deduct": float(deduct),
        "target_price": 500.0,
    } for (item_id, sku_id), deduct
        in recovery.EXPECTED_DISCOUNT_DEDUCTS.items()]


def _records(manifest, *, final=False):
    rows = []
    for record in manifest["draft_records"]:
        sku_ids = list(record["final_sku_ids"])
        if not final:
            sku_ids = sorted(set(sku_ids) - set(record["add_sku_ids"]))
        expected_prices = {row["sku_id"]: row["signup_price"]
                           for row in record["expected_sku_rows"]}
        snapshot = {"item_id": record["item_id"], "sku_ids": sku_ids,
                    "prices": expected_prices}
        rows.append({
            "item_id": record["item_id"], "record_id": record["record_id"],
            "status": "已发布" if final else "草稿",
            "sku_count": len(sku_ids), "sku_ids": sku_ids,
            "sku_rows": [{"sku_id": sku_id,
                          "signup_price": expected_prices[sku_id]}
                         for sku_id in sku_ids],
            "snapshot": snapshot, "before_hash": recovery._hash(snapshot),
        })
    return rows


def _web_result(payload, *, phase):
    manifest = payload["manifest"]
    if phase == "commit":
        before = manifest["inspection_baseline"]["new_discount_before_rows"]
        missing = [(row["item_id"], row["sku_id"])
                   for row in before if row["state"] == "missing"]
        correct = [(row["item_id"], row["sku_id"])
                   for row in before if row["state"] == "correct"]
        return {
            "ok": True, "phase": phase, "platform_write": True,
            "scope_sha256": payload["scope_sha256"],
            "inspection_baseline": manifest["inspection_baseline"],
            "discount_rows_written": len(missing),
            "discount_rows_already_correct": len(correct),
            "discount_pairs_written": [list(pair) for pair in missing],
            "discount_pairs_already_correct": [list(pair) for pair in correct],
            "draft_records_updated": 6,
            "draft_records_published": 6, "reservation_consumed": True,
            "patched_record_ids": sorted(
                row["record_id"] for row in recovery.DRAFT_RECORDS.values()),
            "published_record_ids": sorted(
                row["record_id"] for row in recovery.DRAFT_RECORDS.values()),
            "checkpoints": recovery.EXPECTED_COMMIT_CHECKPOINTS,
            "inspect_scope_unchanged": True,
        }
    discount_state = "active" if phase == "readback" else "missing"
    out = {
        "ok": True, "phase": phase, "identity": recovery.IDENTITY,
        "scope_sha256": payload["scope_sha256"], "platform_write": False,
        "draft_records": _records(manifest, final=phase == "readback"),
        "discount_rows": [
            {
             "item_id": row["taobao_item_id"],
             "sku_id": row["taobao_sku_id"],
             "expected_deduct": row["deduct"],
             "state": discount_state,
             "activity_id": recovery.DISCOUNT_ACTIVITY_ID}
            for row in manifest["discount_rows"]],
    }
    protected = []
    for row in manifest["protected_records"]:
        snapshot = {"item_id": row["item_id"], "record_id": row["record_id"],
                    "status": "已发布",
                    "sku_ids": [f"{row['item_id']}-{index}"
                                for index in range(row["sku_count"])]}
        before_hash = (manifest.get("inspection_baseline") or {}).get(
            "protected_record_before_hashes", {}).get(
                row["item_id"], recovery._hash(snapshot))
        protected.append({
            **row, "status": "已发布",
            "sku_ids": snapshot["sku_ids"], "snapshot": snapshot,
            "before_hash": before_hash, "after_hash": before_hash,
        })
    legacy_rows = [{
        "item_id": "1036312802226", "sku_id": str(8000000000000 + index),
        "actual_deduct": "10.00", "activity_id": "143900000002",
        "activity_status": "进行中",
    } for index in range(53)]
    out.update({
        "protected_records": protected,
        "legacy_discount_baseline": {
            "row_count": 53, "rows": legacy_rows,
            "sha256": (manifest.get("inspection_baseline") or {}).get(
                "legacy_discount_sha256", recovery._hash(legacy_rows)),
        },
        "all_record_ids": [row["record_id"]
                           for row in manifest["draft_records"]]
                          + [row["record_id"]
                             for row in manifest["protected_records"]],
        "excluded_item_ids": [recovery.ZERO_SALES_EXCLUDED_ITEM_ID,
                              recovery.WAREHOUSE_EXCLUDED_ITEM_ID],
        "artifact_sha256": "f" * 64,
    })
    if phase == "inspect":
        out["reservation_token"] = "reservation-token-1234567890"
        out["reservation_active"] = True
        out["lease_expires_at_epoch"] = (
            datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()
        add_rows = [row for record in manifest["draft_records"]
                    for row in record["add_rows"]]
        out["candidate_price_evidence"] = {
            "ok": True,
            "records": [{
                "item_id": row["item_id"], "sku_id": row["sku_id"],
                "sku_name": f"SKU-{row['sku_id']}",
                "current_list_price": row["signup_price"],
                "min_list_price": row["signup_price"],
                "max_eligible_activity_price": row["signup_price"],
            } for row in add_rows],
            "requested_sku_count": 8, "observed_sku_count": 8,
            "sha256": "a" * 64,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "source": "qianniu_selectable_item_list",
            "selection_guard": {"checked": 0, "zero_selected": True},
        }
    if phase == "readback":
        out["custom_sku_ids"] = manifest["final_scope"]["custom_sku_ids"]
        out["inspection_baseline"] = manifest["inspection_baseline"]
    return out


def _patch_scope(db, monkeypatch):
    rows = _signup_rows()
    monkeypatch.setattr(
        campaign_policy_service, "require_policy",
        lambda: {"_sha256": recovery.EXPECTED_POLICY_SHA256})
    monkeypatch.setattr(
        campaign_service, "build_signup_rows",
        lambda *_a, **_k: (list(rows), {"rows": len(rows)}))
    monkeypatch.setattr(
        campaign_service, "build_discount_rows",
        lambda *_a, **_k: (_discount_rows(), {"rows": 8}))
    monkeypatch.setattr(
        campaign_execution_service, "scope_sha256",
        lambda **_kwargs: recovery.EXPECTED_TARGET_SCOPE_SHA256)


def _run(db, *, mode="execute", **overrides):
    values = {
        "workflow_key": recovery.WORKFLOW_KEY,
        "expected_plan_id": 8,
        "expected_status": "alarmed",
        "recovery_version": recovery.RECOVERY_VERSION,
        "mode": mode,
        "confirmation": (recovery.EXECUTE_CONFIRMATION
                         if mode == "execute" else recovery.READBACK_CONFIRMATION),
        "target_scope_sha256": recovery.EXPECTED_TARGET_SCOPE_SHA256,
    }
    values.update(overrides)
    return recovery.recover_plan8_final_v6(db, **values)


def test_plan8_v6_route_is_narrowly_allowlisted_and_v5_is_preserved():
    assert dependencies.CAMPAIGN_PLAN8_FINAL_RECOVERY_V6_PATH == (
        "/api/campaigns/recover-super88-plan8-final-v6")
    assert dependencies.CAMPAIGN_PLAN8_FINAL_RECOVERY_V6_PATH in (
        dependencies.CAMPAIGN_PREPARE_SERVICE_PATHS)
    assert dependencies.CAMPAIGN_PLAN8_FINAL_RECOVERY_V5_PATH in (
        dependencies.CAMPAIGN_PREPARE_SERVICE_PATHS)
    assert dependencies.CAMPAIGN_PLAN8_FINAL_RECOVERY_V4_PATH in (
        dependencies.CAMPAIGN_PREPARE_SERVICE_PATHS)
    assert recovery.RECOVERY_VERSION == 6
    assert recovery.OPERATION == "plan8_final_recovery_v6"
    assert len(recovery.DRAFT_RECORDS) == 6
    assert sum(len(row["add_sku_ids"])
               for row in recovery.DRAFT_RECORDS.values()) == 8
    assert recovery.EXPECTED_TARGET_ROW_COUNT == 78
    assert recovery.EXPECTED_TARGET_CUSTOM_ROW_COUNT == 18


def test_plan8_v6_discount_scope_matches_web_agent_wire_contract(
        db_session, monkeypatch):
    source_rows = _discount_rows()
    monkeypatch.setattr(
        campaign_service, "build_discount_rows",
        lambda *_a, **_k: (source_rows, {"rows": 8}))

    rows, error = recovery._discount_scope(db_session, object())

    assert error is None
    assert len(rows) == 8
    assert all(set(row) == {
        "taobao_item_id", "taobao_sku_id", "sku_code", "deduct",
        "target_price",
    } for row in rows)
    assert {(row["taobao_item_id"], row["taobao_sku_id"])
            for row in rows} == recovery.ADD_PAIRS
    assert all(row["sku_code"] and float(row["target_price"]) > 0
               for row in rows)
    assert {(row["taobao_item_id"], row["taobao_sku_id"]): row["deduct"]
            for row in rows} == recovery.EXPECTED_DISCOUNT_DEDUCTS


def test_plan8_v2_is_permanently_retired_in_runtime(db_session, monkeypatch):
    monkeypatch.setattr(
        web_agent_service, "inspect_plan8_final_discount_supplement",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("retired V2 must not touch Web-Agent")))
    result = recovery_v2.recover_plan8_final_v2(
        db_session, workflow_key=recovery_v2.WORKFLOW_KEY,
        expected_plan_id=8, expected_status="alarmed", recovery_version=2)
    assert recovery_v2.V2_RETIRED is True
    assert result["error"] == "plan8_final_v2_retired_use_v3"


def test_plan8_v6_exact_draft_and_discount_contract(db_session, monkeypatch):
    db_session.add(_plan())
    db_session.commit()
    _seed_prerequisites(db_session)
    _patch_scope(db_session, monkeypatch)
    calls = []

    def fake_web(_db, *, payload, **_kwargs):
        calls.append(payload)
        if payload["phase"] == "commit":
            claimed = db_session.get(
                CampaignExecutionAttempt, payload["attempt_id"])
            assert claimed.state == "write_claimed"
            assert claimed.write_claimed is True
            assert db_session.get(CampaignPlan, 8).status == "resume_executing"
            assert payload["claim_verification"] == {
                "attempt_id": claimed.id,
                "workflow_key": recovery.WORKFLOW_KEY,
                "plan_id": 8,
                "operation": recovery.OPERATION,
                "scope_sha256": claimed.scope_sha256,
                "inspect_scope_sha256": calls[0]["scope_sha256"],
                "reservation_token_sha256": recovery._hash(
                    "reservation-token-1234567890"),
            }
        return _web_result(payload, phase=payload["phase"])

    monkeypatch.setattr(web_agent_service, "recover_plan8_final_v6", fake_web)
    result = _run(db_session)
    assert result["ok"] is True
    assert [call["phase"] for call in calls] == [
        "inspect", "commit", "readback"]
    assert calls[1]["reservation_token"] == "reservation-token-1234567890"
    assert calls[1]["inspect_scope_sha256"] == calls[0]["scope_sha256"]
    assert calls[1]["scope_sha256"] != calls[0]["scope_sha256"]
    for record in calls[0]["manifest"]["draft_records"]:
        for row in record["add_rows"]:
            assert set(row) == {
                "item_id", "sku_id", "signup_price", "is_custom"}
    assert result["verification"]["record_count"] == 6
    assert result["verification"]["sku_count"] == 78
    assert result["verification"]["custom_sku_count"] == 18
    attempt = db_session.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.operation == recovery.OPERATION,
    )).scalar_one()
    assert attempt.state == "completed"
    assert attempt.write_claimed is True
    assert attempt.platform_write_observed is True
    assert db_session.execute(select(CampaignExecutionAttempt)).scalars().all().__len__() == (
        len(recovery.PREREQUISITE_ATTEMPTS) + 1)
    manifest = attempt.result_summary["manifest"]
    assert manifest["inspection_baseline"]["legacy_discount_row_count"] == 53
    assert len(manifest["inspection_baseline"]["new_discount_before_rows"]) == 8
    assert set(manifest["inspection_baseline"][
        "protected_record_before_hashes"]) == set(recovery.PROTECTED_RECORDS)
    assert "reservation-token-1234567890" not in json.dumps(manifest)
    assert db_session.get(CampaignPlan, 8).status == "reconciled"


def test_plan8_v6_busy_does_not_create_or_consume_claim(
        db_session, monkeypatch):
    db_session.add(_plan())
    db_session.commit()
    _seed_prerequisites(db_session)
    _patch_scope(db_session, monkeypatch)
    monkeypatch.setattr(
        web_agent_service, "recover_plan8_final_v6",
        lambda *_a, **_k: {
            "ok": False, "error": "taobao_profile_busy",
            "step": "pre_write_busy", "busy": True,
            "claim_created": False, "retry_safe": True,
            "platform_write": False,
        })
    result = _run(db_session)
    assert result["error"] == "plan8_final_v6_pre_write_busy"
    assert result["write_claim_created"] is False
    assert db_session.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.operation == recovery.OPERATION,
    )).scalar_one_or_none() is None


def test_plan8_v6_claimed_failure_never_reexecutes_and_readback_is_read_only(
        db_session, monkeypatch):
    db_session.add(_plan())
    db_session.commit()
    _seed_prerequisites(db_session)
    _patch_scope(db_session, monkeypatch)
    phases = []

    def first_web(_db, *, payload, **_kwargs):
        phases.append(payload["phase"])
        if payload["phase"] == "commit":
            return {"ok": False, "error": "browser_closed",
                    "platform_write": None, "step": "publish"}
        return _web_result(payload, phase=payload["phase"])

    monkeypatch.setattr(web_agent_service, "recover_plan8_final_v6", first_web)
    failed = _run(db_session)
    assert failed["error"] == "plan8_final_v6_commit_failed_no_retry"
    assert phases == ["inspect", "commit"]
    replay = _run(db_session)
    assert replay["error"] == "plan8_final_v6_already_claimed_no_retry"
    assert phases == ["inspect", "commit"]

    readback_payloads = []

    def readback_web(_db, *, payload, **_kwargs):
        readback_payloads.append(payload)
        return _web_result(payload, phase="readback")

    monkeypatch.setattr(
        web_agent_service, "recover_plan8_final_v6", readback_web)
    verified = _run(db_session, mode="readback")
    assert verified["ok"] is True
    assert verified["readback_only"] is True
    assert verified["execution_boundary"]["platform_write"] is False
    verified_attempt = db_session.get(
        CampaignExecutionAttempt, failed["attempt_id"])
    assert verified_attempt.platform_write_observed is None
    assert readback_payloads == [{
        "phase": "readback",
        "scope_sha256": db_session.execute(
            select(CampaignExecutionAttempt).where(
                CampaignExecutionAttempt.operation == recovery.OPERATION)
        ).scalar_one().scope_sha256,
        "manifest": db_session.execute(
            select(CampaignExecutionAttempt).where(
                CampaignExecutionAttempt.operation == recovery.OPERATION)
        ).scalar_one().result_summary["manifest"],
        "attempt_id": failed["attempt_id"],
    }]


def test_plan8_v6_rejects_discount_amount_drift(db_session, monkeypatch):
    db_session.add(_plan())
    db_session.commit()
    _seed_prerequisites(db_session)
    _patch_scope(db_session, monkeypatch)
    rows = _discount_rows()
    rows[0]["deduct"] += 0.01
    monkeypatch.setattr(
        campaign_service, "build_discount_rows",
        lambda *_a, **_k: (rows, {"rows": 8}))
    monkeypatch.setattr(
        web_agent_service, "recover_plan8_final_v6",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("price drift must stop before Web-Agent")))
    result = _run(db_session)
    assert result["error"] == "plan8_final_v6_discount_amount_drift"


def test_plan8_v6_inspection_accepts_empty_activity_prices_before_full_patch(
        db_session, monkeypatch):
    db_session.add(_plan())
    db_session.commit()
    _seed_prerequisites(db_session)
    _patch_scope(db_session, monkeypatch)

    def empty_price_inspect(_db, *, payload, **_kwargs):
        phase = payload["phase"]
        result = _web_result(payload, phase=phase)
        if phase == "inspect":
            for record in result["draft_records"]:
                for row in record["sku_rows"]:
                    row["signup_price"] = None
        return result

    monkeypatch.setattr(
        web_agent_service, "recover_plan8_final_v6", empty_price_inspect)
    result = _run(db_session)
    assert result["ok"] is True
    assert result["verification"]["record_count"] == 6
    assert result["verification"]["sku_count"] == 78
    assert result["verification"]["custom_sku_count"] == 18


def test_plan8_v6_inspection_rejects_sku_or_extra_record_before_claim(
        db_session, monkeypatch):
    db_session.add(_plan())
    db_session.commit()
    _seed_prerequisites(db_session)
    _patch_scope(db_session, monkeypatch)

    def bad_inspect(_db, *, payload, **_kwargs):
        result = _web_result(payload, phase="inspect")
        result["draft_records"][0]["sku_rows"][0]["sku_id"] = "9999999999999"
        result["all_record_ids"].append("unexpected-record")
        return result

    monkeypatch.setattr(
        web_agent_service, "recover_plan8_final_v6", bad_inspect)
    result = _run(db_session)
    assert result["error"] == "plan8_final_v6_inspection_blocked"
    assert db_session.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.operation == recovery.OPERATION,
    )).scalar_one_or_none() is None


def test_plan8_v6_rechecks_erp_scope_after_reservation_before_claim(
        db_session, monkeypatch):
    db_session.add(_plan())
    db_session.commit()
    _seed_prerequisites(db_session)
    _patch_scope(db_session, monkeypatch)
    rows = _signup_rows()
    calls = {"count": 0}

    def changing_rows(*_args, **_kwargs):
        calls["count"] += 1
        current = rows if calls["count"] == 1 else rows[:-1]
        return list(current), {"rows": len(current)}

    monkeypatch.setattr(campaign_service, "build_signup_rows", changing_rows)
    monkeypatch.setattr(
        web_agent_service, "recover_plan8_final_v6",
        lambda _db, *, payload, **_kwargs:
        _web_result(payload, phase="inspect"))
    result = _run(db_session)
    assert result["error"] == (
        "plan8_final_v6_erp_scope_changed_after_reservation")
    assert db_session.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.operation == recovery.OPERATION,
    )).scalar_one_or_none() is None


def test_plan8_v6_multiple_attempt_scopes_fail_closed(db_session):
    db_session.add(_plan())
    for index in range(2):
        db_session.add(CampaignExecutionAttempt(
            id=f"{index + 1:024x}", plan_id=8,
            workflow_key=recovery.WORKFLOW_KEY, operation=recovery.OPERATION,
            scope_sha256=str(index) * 64, state="unknown_no_retry",
            write_claimed=True, automatic_retry_allowed=False,
        ))
    db_session.commit()
    result = _run(db_session)
    assert result["error"] == "plan8_final_v6_attempt_scope_ambiguous"
    assert result["attempt_count"] == 2


def test_web_agent_v3_busy_response_is_normalized_without_job(monkeypatch):
    monkeypatch.setattr(web_agent_service, "_post", lambda *_a, **_k: {
        "ok": False, "error": "taobao_profile_busy",
        "step": "pre_write_busy", "claim_created": False,
        "retry_safe": True, "platform_write": False,
    })
    result = web_agent_service.recover_plan8_final_v6(
        object(), payload={"phase": "inspect"})
    assert result["busy"] is True
    assert result["pre_write_busy"] is True
    assert result["platform_write"] is False


def test_web_agent_v3_carries_reservation_expiry_from_inspect_envelope(
        monkeypatch):
    monkeypatch.setattr(web_agent_service, "_post", lambda *_a, **_k: {
        "ok": True, "job": "job1", "lease_expires_at_epoch": 12345.0,
    })
    monkeypatch.setattr(web_agent_service, "wait_job", lambda *_a, **_k: {
        "result": {"ok": True, "reservation_token": "opaque"},
    })
    result = web_agent_service.recover_plan8_final_v6(
        object(), payload={"phase": "inspect"})
    assert result["lease_expires_at_epoch"] == 12345.0


def test_plan8_v6_cli_accepts_only_fixed_execute_or_readback(monkeypatch):
    valid = {
        "workflow_key": recovery.WORKFLOW_KEY, "plan_id": 8,
        "expected_status": "alarmed", "recovery_version": 6,
        "mode": "readback",
        "confirmation": recovery.READBACK_CONFIRMATION,
        "target_scope_sha256": recovery.EXPECTED_TARGET_SCOPE_SHA256,
    }
    monkeypatch.setattr(cli.sys, "stdin", type("Input", (), {
        "buffer": io.BytesIO(json.dumps(valid).encode())})())
    assert json.loads(cli._read_payload()) == valid
    invalid = {**valid, "plan_id": 7}
    monkeypatch.setattr(cli.sys, "stdin", type("Input", (), {
        "buffer": io.BytesIO(json.dumps(invalid).encode())})())
    try:
        cli._read_payload()
    except ValueError as exc:
        assert "固化范围" in str(exc)
    else:
        raise AssertionError("drifted payload must be rejected")

    script = Path(__file__).parents[2] / "scripts" / (
        "campaign_recover_plan8_final_v6_nas.ps1")
    text = script.read_text(encoding="utf-8-sig")
    assert text.isascii()
    assert "[switch]$ExecuteOnce" in text
    assert "if ($ExecuteOnce -eq $ReadbackOnly)" in text
    assert recovery.EXECUTE_CONFIRMATION in text
    assert recovery.READBACK_CONFIRMATION in text


def test_plan8_v6_candidate_price_evidence_is_fresh_and_hard_gated(
        db_session, monkeypatch):
    db_session.add(_plan())
    db_session.commit()
    _seed_prerequisites(db_session)
    _patch_scope(db_session, monkeypatch)

    def stale(_db, *, payload, **_kwargs):
        result = _web_result(payload, phase="inspect")
        evidence = result["candidate_price_evidence"]
        evidence["observed_at"] = (
            datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        evidence["records"][0]["current_list_price"] = "999.00"
        return result

    monkeypatch.setattr(web_agent_service, "recover_plan8_final_v6", stale)
    result = _run(db_session)
    assert result["error"] == "plan8_final_v6_inspection_blocked"
    problems = result["inspection"]["candidate_price_evidence"]["problems"]
    assert any("evidence_stale_or_future" in row.get("reasons", [])
               for row in problems)
    assert any("current_live_price_not_erp_daily_price" in row.get("reasons", [])
               for row in problems)
    assert db_session.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.operation == recovery.OPERATION,
    )).scalar_one_or_none() is None


def _bound_candidate_evidence(manifest):
    rows = []
    for pair, fixed in sorted(recovery.BOUND_EVIDENCE_ROWS.items()):
        rows.append({
            "item_id": pair[0], "sku_id": pair[1],
            "draft_record_id": fixed[0],
            "target_merchant_code": fixed[1],
            "target_product_list_price": fixed[2],
            "target_stock": fixed[3],
            "target_signup_price": fixed[4],
            "source_sku_id": fixed[5],
            "source_product_list_price": fixed[6],
            "source_min_list_price": fixed[7],
            "platform_rule_ratio": "0.75",
        })
    return {
        "ok": True, "records": rows,
        "missing_sku_ids": sorted(pair[1] for pair in recovery.BOUND_EVIDENCE_ROWS),
        "requested_sku_count": 8, "observed_sku_count": 0,
        "candidate_items_scanned": 50, "page_count": 6,
        "candidate_sha256": "b" * 64,
        "official_product_export_sha256": recovery.BOUND_PRODUCT_EXPORT_SHA256,
        "sha256": "a" * 64,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "source": recovery.BOUND_DRAFT_EVIDENCE_SOURCE,
        "selection_guard": {"checked": 0, "zero_selected": True},
    }


def _bound_manifest(monkeypatch):
    rows = _signup_rows()
    for row in rows:
        fixed = recovery.BOUND_EVIDENCE_ROWS.get(
            (row["taobao_item_id"], row["taobao_sku_id"]))
        if fixed is not None:
            row["price"] = float(fixed[4])
    monkeypatch.setattr(
        campaign_execution_service, "scope_sha256",
        lambda **_kwargs: recovery.EXPECTED_TARGET_SCOPE_SHA256)
    return recovery._fixed_manifest(
        rows, [{"item_id": item, "sku_id": sku, "expected_deduct": amount}
               for (item, sku), amount in recovery.EXPECTED_DISCOUNT_DEDUCTS.items()],
        recovery.EXPECTED_POLICY_SHA256)


def test_plan8_v6_accepts_exact_bound_draft_evidence(monkeypatch):
    manifest = _bound_manifest(monkeypatch)
    ok, detail = recovery._validate_candidate_price_evidence(
        {"candidate_price_evidence": _bound_candidate_evidence(manifest)}, manifest)
    assert ok is True
    assert detail["source"] == recovery.BOUND_DRAFT_EVIDENCE_SOURCE
    assert len(detail["rows"]) == 8
    assert detail["missing_sku_ids"]


@pytest.mark.parametrize("drift", [
    "export_sha", "merchant_code", "stock", "target_ratio",
    "source_ratio", "platform_ratio", "candidate_missing",
])
def test_plan8_v6_bound_draft_evidence_drift_is_rejected(monkeypatch, drift):
    manifest = _bound_manifest(monkeypatch)
    evidence = _bound_candidate_evidence(manifest)
    if drift == "export_sha":
        evidence["official_product_export_sha256"] = "0" * 64
    elif drift == "merchant_code":
        evidence["records"][0]["target_merchant_code"] = "WRONG"
    elif drift == "stock":
        evidence["records"][0]["target_stock"] = 99
    elif drift == "target_ratio":
        evidence["records"][0]["target_product_list_price"] = "6941.00"
    elif drift == "source_ratio":
        evidence["records"][0]["source_min_list_price"] = "5954.00"
    elif drift == "platform_ratio":
        evidence["records"][0]["platform_rule_ratio"] = "0.74"
    else:
        evidence["missing_sku_ids"] = evidence["missing_sku_ids"][1:]
    ok, detail = recovery._validate_candidate_price_evidence(
        {"candidate_price_evidence": evidence}, manifest)
    assert ok is False
    assert detail["problems"] or drift in {"export_sha", "candidate_missing"}


def test_plan8_v6_bound_drift_creates_no_attempt(db_session, monkeypatch):
    db_session.add(_plan())
    db_session.commit()
    _seed_prerequisites(db_session)
    _patch_scope(db_session, monkeypatch)

    def drift(_db, *, payload, **_kwargs):
        result = _web_result(payload, phase="inspect")
        result["candidate_price_evidence"] = _bound_candidate_evidence(
            payload["manifest"])
        result["candidate_price_evidence"]["records"][0]["target_stock"] = 99
        return result

    monkeypatch.setattr(web_agent_service, "recover_plan8_final_v6", drift)
    result = _run(db_session)
    assert result["error"] == "plan8_final_v6_inspection_blocked"
    assert db_session.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.operation == recovery.OPERATION,
    )).scalar_one_or_none() is None


def test_plan8_v6_commit_accepts_written_plus_already_correct_exact_union(
        monkeypatch):
    rows = _signup_rows()
    monkeypatch.setattr(
        campaign_execution_service, "scope_sha256",
        lambda **_kwargs: recovery.EXPECTED_TARGET_SCOPE_SHA256)
    normalized_discounts = [{
        "item_id": item_id, "sku_id": sku_id, "expected_deduct": value,
    } for (item_id, sku_id), value
        in recovery.EXPECTED_DISCOUNT_DEDUCTS.items()]
    manifest = recovery._fixed_manifest(
        rows, normalized_discounts, recovery.EXPECTED_POLICY_SHA256)
    legacy = [{
        "item_id": "1036312802226", "sku_id": str(8000000000000 + index),
        "actual_deduct": "10.00", "activity_id": "143900000002",
        "activity_status": "进行中",
    } for index in range(53)]
    manifest["inspection_baseline"] = {
        "legacy_discount_rows": legacy,
        "legacy_discount_sha256": recovery._hash(legacy),
    }
    scope_sha = recovery._hash(manifest)
    ordered = sorted(recovery.ADD_PAIRS)
    result = {
        "ok": True, "platform_write": True, "scope_sha256": scope_sha,
        "inspection_baseline": manifest["inspection_baseline"],
        "discount_pairs_written": [list(pair) for pair in ordered[:4]],
        "discount_pairs_already_correct": [list(pair) for pair in ordered[4:]],
        "discount_rows_written": 4, "discount_rows_already_correct": 4,
        "draft_records_updated": 6, "draft_records_published": 6,
        "patched_record_ids": sorted(
            row["record_id"] for row in recovery.DRAFT_RECORDS.values()),
        "published_record_ids": sorted(
            row["record_id"] for row in recovery.DRAFT_RECORDS.values()),
        "checkpoints": recovery.EXPECTED_COMMIT_CHECKPOINTS,
        "inspect_scope_unchanged": True, "reservation_consumed": True,
    }
    assert recovery.validate_commit(result, manifest, scope_sha)[0] is True
    result["discount_pairs_already_correct"] = []
    assert recovery.validate_commit(result, manifest, scope_sha)[0] is False


def test_plan8_v6_claim_verification_is_exact_and_read_only(db_session):
    manifest = {"inspection_baseline": {
        "inspect_scope_sha256": "1" * 64,
        "reservation_token_sha256": "2" * 64,
        "reservation_expires_at_epoch": (
            datetime.now(timezone.utc) + timedelta(hours=1)).timestamp(),
    }}
    scope_sha = recovery._hash(manifest)
    attempt = CampaignExecutionAttempt(
        id="e" * 24, plan_id=8, workflow_key=recovery.WORKFLOW_KEY,
        operation=recovery.OPERATION, scope_sha256=scope_sha,
        state="write_claimed", write_claimed=True,
        write_claimed_at=datetime.now(timezone.utc),
        request_id="plan8-v3-claim-test", automatic_retry_allowed=False,
        result_summary={"manifest": manifest},
    )
    db_session.add(attempt)
    db_session.commit()
    common = {
        "attempt_id": attempt.id, "workflow_key": recovery.WORKFLOW_KEY,
        "plan_id": 8, "operation": recovery.OPERATION,
        "scope_sha256": scope_sha, "inspect_scope_sha256": "1" * 64,
        "reservation_token_sha256": "2" * 64,
    }
    verified = recovery.verify_plan8_final_v6_claim(db_session, **common)
    assert verified["ok"] is True
    assert verified["execution_boundary"]["platform_write"] is False
    assert "manifest" not in verified
    assert recovery.verify_plan8_final_v6_claim(
        db_session, **{**common, "scope_sha256": "3" * 64})["ok"] is False


def test_plan8_v6_request_requires_explicit_mode_confirmation_and_scope(
        db_session):
    denied = recovery.recover_plan8_final_v6(
        db_session, workflow_key=recovery.WORKFLOW_KEY, expected_plan_id=8,
        expected_status="alarmed", recovery_version=6, mode="execute",
        confirmation="", target_scope_sha256=recovery.EXPECTED_TARGET_SCOPE_SHA256)
    assert denied["error"] == "plan8_final_v6_request_not_allowed"


def test_plan8_v6_claim_verify_machine_identity_is_path_scoped(monkeypatch):
    monkeypatch.setattr(
        dependencies.settings_service, "get",
        lambda _db, key, **_kwargs: "secret" if key == "web_agent_token" else None)
    path = dependencies.CAMPAIGN_PLAN8_FINAL_RECOVERY_V6_CLAIM_VERIFY_PATH
    assert dependencies.machine_identity_for_key(
        "secret", object(), path=path
    ) == "machine:web-agent-plan8-v6-claim-verify"
    assert dependencies.machine_identity_for_key(
        "secret", object(), path="/api/campaigns"
    ) is None
