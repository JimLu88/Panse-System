import io
import json
from datetime import datetime

from sqlalchemy import select

from app import dependencies
from app.cli import campaign_recover_plan8_final_v3 as cli
from app.models.campaign import CampaignExecutionAttempt, CampaignPlan
from app.services import (
    campaign_execution_service,
    campaign_plan8_final_recovery_v3_service as recovery,
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
        operation, state, claimed = expected
        db.add(CampaignExecutionAttempt(
            id=attempt_id, plan_id=8, workflow_key=recovery.WORKFLOW_KEY,
            operation=operation, scope_sha256=(attempt_id * 3)[:64],
            state=state, write_claimed=claimed,
            platform_write_observed=claimed, automatic_retry_allowed=False,
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
            custom = custom_left > 0
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
        return {
            "ok": True, "phase": phase, "platform_write": True,
            "scope_sha256": payload["scope_sha256"],
            "inspection_baseline": manifest["inspection_baseline"],
            "discount_rows_written": 8, "draft_records_updated": 2,
            "draft_records_published": 6, "reservation_consumed": True,
        }
    discount_state = "active" if phase == "readback" else "missing"
    out = {
        "ok": True, "phase": phase, "identity": recovery.IDENTITY,
        "scope_sha256": payload["scope_sha256"], "platform_write": False,
        "draft_records": _records(manifest, final=phase == "readback"),
        "discount_rows": [
            {**row, "state": discount_state,
             "activity_id": "143900000001"}
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
    legacy_rows = [{"sku_id": str(index)} for index in range(53)]
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


def test_plan8_v3_route_is_narrowly_allowlisted():
    assert dependencies.CAMPAIGN_PLAN8_FINAL_RECOVERY_V3_PATH == (
        "/api/campaigns/recover-super88-plan8-final-v3")
    assert dependencies.CAMPAIGN_PLAN8_FINAL_RECOVERY_V3_PATH in (
        dependencies.CAMPAIGN_PREPARE_SERVICE_PATHS)
    assert recovery.RECOVERY_VERSION == 3
    assert len(recovery.DRAFT_RECORDS) == 6
    assert sum(len(row["add_sku_ids"])
               for row in recovery.DRAFT_RECORDS.values()) == 8
    assert recovery.EXPECTED_TARGET_ROW_COUNT == 78
    assert recovery.EXPECTED_TARGET_CUSTOM_ROW_COUNT == 18


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


def test_plan8_v3_exact_draft_and_discount_contract(db_session, monkeypatch):
    db_session.add(_plan())
    db_session.commit()
    _seed_prerequisites(db_session)
    _patch_scope(db_session, monkeypatch)
    calls = []

    def fake_web(_db, *, payload, **_kwargs):
        calls.append(payload)
        return _web_result(payload, phase=payload["phase"])

    monkeypatch.setattr(web_agent_service, "recover_plan8_final_v3", fake_web)
    result = recovery.recover_plan8_final_v3(
        db_session, workflow_key=recovery.WORKFLOW_KEY,
        expected_plan_id=8, expected_status="alarmed", recovery_version=3)
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


def test_plan8_v3_busy_does_not_create_or_consume_claim(
        db_session, monkeypatch):
    db_session.add(_plan())
    db_session.commit()
    _seed_prerequisites(db_session)
    _patch_scope(db_session, monkeypatch)
    monkeypatch.setattr(
        web_agent_service, "recover_plan8_final_v3",
        lambda *_a, **_k: {
            "ok": False, "error": "taobao_profile_busy",
            "step": "pre_write_busy", "busy": True,
            "claim_created": False, "retry_safe": True,
            "platform_write": False,
        })
    result = recovery.recover_plan8_final_v3(
        db_session, workflow_key=recovery.WORKFLOW_KEY,
        expected_plan_id=8, expected_status="alarmed", recovery_version=3)
    assert result["error"] == "plan8_final_v3_pre_write_busy"
    assert result["write_claim_created"] is False
    assert db_session.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.operation == recovery.OPERATION,
    )).scalar_one_or_none() is None


def test_plan8_v3_claimed_failure_never_reexecutes_and_readback_is_read_only(
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

    monkeypatch.setattr(web_agent_service, "recover_plan8_final_v3", first_web)
    failed = recovery.recover_plan8_final_v3(
        db_session, workflow_key=recovery.WORKFLOW_KEY,
        expected_plan_id=8, expected_status="alarmed", recovery_version=3)
    assert failed["error"] == "plan8_final_v3_commit_failed_no_retry"
    assert phases == ["inspect", "commit"]
    replay = recovery.recover_plan8_final_v3(
        db_session, workflow_key=recovery.WORKFLOW_KEY,
        expected_plan_id=8, expected_status="alarmed", recovery_version=3)
    assert replay["error"] == "plan8_final_v3_already_claimed_no_retry"
    assert phases == ["inspect", "commit"]

    monkeypatch.setattr(
        web_agent_service, "recover_plan8_final_v3",
        lambda _db, *, payload, **_kwargs:
        _web_result(payload, phase="readback"))
    verified = recovery.recover_plan8_final_v3(
        db_session, workflow_key=recovery.WORKFLOW_KEY,
        expected_plan_id=8, expected_status="alarmed", recovery_version=3,
        mode="readback")
    assert verified["ok"] is True
    assert verified["readback_only"] is True
    assert verified["execution_boundary"]["platform_write"] is False


def test_plan8_v3_rejects_discount_amount_drift(db_session, monkeypatch):
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
        web_agent_service, "recover_plan8_final_v3",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("price drift must stop before Web-Agent")))
    result = recovery.recover_plan8_final_v3(
        db_session, workflow_key=recovery.WORKFLOW_KEY,
        expected_plan_id=8, expected_status="alarmed", recovery_version=3)
    assert result["error"] == "plan8_final_v3_discount_amount_drift"


def test_plan8_v3_inspection_rejects_price_or_extra_record_before_claim(
        db_session, monkeypatch):
    db_session.add(_plan())
    db_session.commit()
    _seed_prerequisites(db_session)
    _patch_scope(db_session, monkeypatch)

    def bad_inspect(_db, *, payload, **_kwargs):
        result = _web_result(payload, phase="inspect")
        result["draft_records"][0]["sku_rows"][0]["signup_price"] = "0.01"
        result["all_record_ids"].append("unexpected-record")
        return result

    monkeypatch.setattr(
        web_agent_service, "recover_plan8_final_v3", bad_inspect)
    result = recovery.recover_plan8_final_v3(
        db_session, workflow_key=recovery.WORKFLOW_KEY,
        expected_plan_id=8, expected_status="alarmed", recovery_version=3)
    assert result["error"] == "plan8_final_v3_inspection_blocked"
    assert db_session.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.operation == recovery.OPERATION,
    )).scalar_one_or_none() is None


def test_plan8_v3_rechecks_erp_scope_after_reservation_before_claim(
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
        web_agent_service, "recover_plan8_final_v3",
        lambda _db, *, payload, **_kwargs:
        _web_result(payload, phase="inspect"))
    result = recovery.recover_plan8_final_v3(
        db_session, workflow_key=recovery.WORKFLOW_KEY,
        expected_plan_id=8, expected_status="alarmed", recovery_version=3)
    assert result["error"] == (
        "plan8_final_v3_erp_scope_changed_after_reservation")
    assert db_session.execute(select(CampaignExecutionAttempt).where(
        CampaignExecutionAttempt.operation == recovery.OPERATION,
    )).scalar_one_or_none() is None


def test_plan8_v3_multiple_attempt_scopes_fail_closed(db_session):
    db_session.add(_plan())
    for index in range(2):
        db_session.add(CampaignExecutionAttempt(
            id=f"{index + 1:024x}", plan_id=8,
            workflow_key=recovery.WORKFLOW_KEY, operation=recovery.OPERATION,
            scope_sha256=str(index) * 64, state="unknown_no_retry",
            write_claimed=True, automatic_retry_allowed=False,
        ))
    db_session.commit()
    result = recovery.recover_plan8_final_v3(
        db_session, workflow_key=recovery.WORKFLOW_KEY,
        expected_plan_id=8, expected_status="alarmed", recovery_version=3)
    assert result["error"] == "plan8_final_v3_attempt_scope_ambiguous"
    assert result["attempt_count"] == 2


def test_web_agent_v3_busy_response_is_normalized_without_job(monkeypatch):
    monkeypatch.setattr(web_agent_service, "_post", lambda *_a, **_k: {
        "ok": False, "error": "taobao_profile_busy",
        "step": "pre_write_busy", "claim_created": False,
        "retry_safe": True, "platform_write": False,
    })
    result = web_agent_service.recover_plan8_final_v3(
        object(), payload={"phase": "inspect"})
    assert result["busy"] is True
    assert result["pre_write_busy"] is True
    assert result["platform_write"] is False


def test_plan8_v3_cli_accepts_only_fixed_execute_or_readback(monkeypatch):
    valid = {
        "workflow_key": recovery.WORKFLOW_KEY, "plan_id": 8,
        "expected_status": "alarmed", "recovery_version": 3,
        "mode": "readback",
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
