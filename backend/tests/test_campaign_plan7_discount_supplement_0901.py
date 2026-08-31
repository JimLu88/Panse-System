import base64
from datetime import datetime
import hashlib

from app.models.campaign import CampaignEvidenceSnapshot, CampaignExecutionAttempt, CampaignPlan
from app import dependencies
from app.cli import campaign_supplement_plan7_single_discount as cli
from app.services import campaign_plan7_discount_supplement_service as svc


def _plan() -> CampaignPlan:
    return CampaignPlan(
        id=7,
        workflow_key=svc.WORKFLOW_KEY,
        name="2026-09-01超级立减更新窗口",
        campaign_type="super_reduce",
        tier="mid",
        start_at=datetime(2026, 9, 1, 0, 0, 0),
        end_at=datetime(2026, 9, 5, 23, 59, 59),
        qn_campaign_title="超级立减",
        status="alarmed",
        remark="official_all_store=true; official_exempt_items=805268708396",
        platform_activity_mode="long_running_update",
    )


def _build_rows():
    return [{
        "taobao_item_id": row["item_id"],
        "taobao_sku_id": row["sku_id"],
        "deduct": float(row["expected_deduct"]),
        "is_placeholder": False,
    } for row in svc.EXPECTED_ROWS]


def _activities():
    rows = []
    for activity_id, facts in svc.ACTIVITY_BUSINESS_FACTS.items():
        imports = f"\n{facts['import_status']}" if facts["import_status"] else ""
        rows.append({
            "activity_id": activity_id,
            "identity_readable": True,
            "status": "进行中",
            "row_text": (
                f"{facts['activity_name']}\nID\n{activity_id}\n自选商品活动\n"
                f"SKU级\n减钱\n开始\n{svc.START_AT}\n结束\n{svc.END_AT}\n"
                f"进行中{imports}\n{facts['created_at']}\n添加商品"),
        })
    return rows


def _rows(*, present: bool):
    return [{
        **row,
        "classification": "correct_effective" if present else "missing",
        "actual_deduct": row["expected_deduct"] if present else None,
        "status": "进行中" if present else None,
        "activity_ids": [svc.TARGET_ACTIVITY_ID] if present else [],
    } for row in svc.EXPECTED_ROWS]


def _artifact():
    raw = b'{"plan7":"supplement-readback"}'
    return {
        "kind": "canonical_visible_readback_json",
        "filename": "plan7-discount-supplement-readback.json",
        "size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "content_b64": base64.b64encode(raw).decode(),
    }


def _read(*, present: bool):
    return {
        "ok": True,
        "web_agent_job_id": "job-post" if present else "job-pre",
        "scope_sha256": svc.SCOPE_SHA256,
        "activity_rows": _activities(),
        "rows": _rows(present=present),
        "platform_summary": {"correct_effective" if present else "missing": 4},
        "artifact": _artifact(),
        "execution_boundary": {"platform_write": False},
    }


def _install(db_session, monkeypatch):
    db_session.add(_plan())
    db_session.commit()
    monkeypatch.setattr(
        svc.campaign_service, "build_discount_rows",
        lambda *_args, **_kwargs: (_build_rows(), {}))
    monkeypatch.setattr(
        svc.campaign_discount_audit_service,
        "persist_single_discount_terminal",
        lambda **_kwargs: "terminal-receipt")


def test_supplement_claims_once_and_verifies_four_exact_rows(
        db_session, monkeypatch):
    _install(db_session, monkeypatch)
    real_build = svc._build_target_xlsx
    build_calls = []

    def rebuild_with_different_package_metadata(*args, **kwargs):
        raw, error = real_build(*args, **kwargs)
        build_calls.append(True)
        # A ZIP package may differ byte-for-byte because Excel metadata changes
        # during the read.  The fixed semantic scope, not raw package identity,
        # is the actual CAS boundary.
        return raw + (b"\x00" * len(build_calls)), error

    monkeypatch.setattr(svc, "_build_target_xlsx",
                        rebuild_with_different_package_metadata)
    reads = iter([_read(present=False), _read(present=True)])
    monkeypatch.setattr(svc, "_platform_read", lambda *_: next(reads))
    writes = []

    def write(_db, *, payload):
        writes.append(payload)
        return {
            "ok": True,
            "submitted": True,
            "web_agent_job_id": "job-write",
            "trigger": {"activity_id": svc.TARGET_ACTIVITY_ID,
                        "action": "添加商品"},
            "platform_submit": {"attempted": True, "control": "确认设置"},
            "official_terminal": {"state": "complete", "ok": 4, "failed": 0},
            "final_import": {"state": "complete", "ok": 4, "failed": 0},
            "execution_boundary": {"platform_write": True},
        }

    monkeypatch.setattr(
        svc.web_agent_service, "supplement_plan7_single_discount", write)
    result = svc.execute_plan7_discount_supplement(
        db_session, request_payload=svc.request_payload())

    assert result["ok"] is True
    assert result["activity_id"] == svc.TARGET_ACTIVITY_ID
    assert result["official_terminal"] == {
        "state": "complete", "ok": 4, "failed": 0}
    assert len(writes) == 1
    assert writes[0]["target_activity_id"] == svc.TARGET_ACTIVITY_ID
    assert writes[0]["scope_sha256"] == svc.SCOPE_SHA256
    assert len(build_calls) == 2
    assert base64.b64decode(writes[0]["xlsx_b64"]).endswith(b"\x00\x00")
    assert svc.FORBIDDEN_PLACEHOLDER_SKU_ID not in {
        row["sku_id"] for row in writes[0]["rows"]}
    attempt = db_session.query(CampaignExecutionAttempt).one()
    assert attempt.state == "completed"
    assert attempt.write_claimed is True
    assert attempt.platform_write_observed is True
    assert attempt.automatic_retry_allowed is False
    snapshot = db_session.query(CampaignEvidenceSnapshot).one()
    assert snapshot.evidence_type == "plan7_discount_supplement_readback"
    assert snapshot.scope_sha256 == svc.SCOPE_SHA256


def test_terminal_failure_consumes_claim_and_blocks_second_call(
        db_session, monkeypatch):
    _install(db_session, monkeypatch)
    monkeypatch.setattr(svc, "_platform_read", lambda *_: _read(present=False))
    writes = []

    def write(_db, *, payload):
        writes.append(payload)
        return {
            "ok": False,
            "submitted": True,
            "web_agent_job_id": "job-write",
            "error": "partial",
            "trigger": {"activity_id": svc.TARGET_ACTIVITY_ID,
                        "action": "添加商品"},
            "official_terminal": {"state": "partial", "ok": 3, "failed": 1},
            "final_import": {"state": "partial", "ok": 3, "failed": 1},
            "execution_boundary": {"platform_write": True},
        }

    monkeypatch.setattr(
        svc.web_agent_service, "supplement_plan7_single_discount", write)
    first = svc.execute_plan7_discount_supplement(
        db_session, request_payload=svc.request_payload())
    second = svc.execute_plan7_discount_supplement(
        db_session, request_payload=svc.request_payload())

    assert first["error"] == (
        "plan7_discount_supplement_terminal_not_exact_no_retry")
    assert second["error"] == (
        "plan7_discount_supplement_attempt_already_claimed_no_retry")
    assert len(writes) == 1
    attempt = db_session.query(CampaignExecutionAttempt).one()
    assert attempt.state == "unknown"
    assert attempt.automatic_retry_allowed is False


def test_prewrite_platform_presence_stops_without_claim(
        db_session, monkeypatch):
    _install(db_session, monkeypatch)
    monkeypatch.setattr(svc, "_platform_read", lambda *_: _read(present=True))
    writes = []
    monkeypatch.setattr(
        svc.web_agent_service, "supplement_plan7_single_discount",
        lambda *_args, **_kwargs: writes.append(True))

    result = svc.execute_plan7_discount_supplement(
        db_session, request_payload=svc.request_payload())

    assert result["error"] == (
        "plan7_discount_supplement_platform_state_not_allowed")
    assert db_session.query(CampaignExecutionAttempt).count() == 0
    assert writes == []


def test_request_and_erp_scope_drift_fail_before_platform(
        db_session, monkeypatch):
    _install(db_session, monkeypatch)
    changed = svc.request_payload()
    changed["rows"][0]["expected_deduct"] = "0.01"
    assert svc.execute_plan7_discount_supplement(
        db_session, request_payload=changed)["error"] == (
            "plan7_discount_supplement_request_not_allowed")

    monkeypatch.setattr(
        svc.campaign_service, "build_discount_rows",
        lambda *_args, **_kwargs: (_build_rows()[:-1], {}))
    result = svc.execute_plan7_discount_supplement(
        db_session, request_payload=svc.request_payload())
    assert result["error"] == "plan7_discount_supplement_erp_scope_drift"
    assert db_session.query(CampaignExecutionAttempt).count() == 0


def test_official_service_route_and_cli_are_bound_to_fixed_scope():
    assert dependencies.CAMPAIGN_PLAN7_DISCOUNT_SUPPLEMENT_PATH in (
        dependencies.CAMPAIGN_PREPARE_SERVICE_PATHS)
    assert dependencies.CAMPAIGN_PLAN7_DISCOUNT_SUPPLEMENT_PATH in cli._URL
    payload = svc.request_payload()
    assert payload["activity_ids"] == list(svc.ACTIVITY_IDS)
    assert payload["target_activity_id"] == svc.TARGET_ACTIVITY_ID
    assert payload["item_id"] == svc.ITEM_ID
    assert len(payload["rows"]) == 4
    assert svc.FORBIDDEN_PLACEHOLDER_SKU_ID not in {
        row["sku_id"] for row in payload["rows"]}
