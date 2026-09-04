import base64
from datetime import datetime
from decimal import Decimal
import hashlib

from app import dependencies
from app.cli import campaign_repair_plan7_sample_cent as cli
from app.cli import campaign_resume_plan7_sample_cent as resume_cli
from app.models.campaign import (
    CampaignEvidenceSnapshot,
    CampaignExecutionAttempt,
    CampaignPlan,
)
from app.models.pricing import PricingSku
from app.models.sku_identity import SkuIdentity
from app.services import campaign_plan7_sample_cent_service as svc


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
        status="reconciled",
        platform_activity_mode="long_running_update",
    )


def _install(db_session):
    db_session.add(_plan())
    observed = datetime(2026, 9, 4, 10, 0, 0)
    for row in svc.EXPECTED_ROWS:
        db_session.add(PricingSku(
            product_code="PPS23980010606",
            product_name="木样块",
            sku=row["sku_code"],
            sku_code=row["sku_code"],
            daily_price=Decimal("30.00"),
            is_custom_placeholder=False,
        ))
        db_session.add(SkuIdentity(
            taobao_item_id=svc.ITEM_ID,
            taobao_sku_id=row["sku_id"],
            merchant_code=row["sku_code"],
            sku_spec=row["sku_code"],
            sku_code=row["sku_code"],
            product_code="PPS23980010606",
            is_custom_placeholder=False,
            identity_sha256="1" * 64,
            first_observed_at=observed,
            last_observed_at=observed,
            latest_sale_state="onsale",
            latest_daily_price=Decimal("30.00"),
            latest_evidence_source="test",
            latest_evidence_sha256="2" * 64,
            conflict_detected=False,
        ))
    db_session.commit()


def _activity_rows():
    return [{
        "activity_id": activity_id,
        "identity_readable": True,
        "status": "进行中",
        "row_text": (
            f"{name}\nID\n{activity_id}\n自选商品活动\nSKU级\n减钱\n"
            f"开始\n{svc.START_AT}\n结束\n{svc.END_AT}\n进行中\n"
            f"{created_at}\n添加商品"),
    } for activity_id, (name, created_at) in
        svc.ACTIVITY_BUSINESS_FACTS.items()]


def _artifact(raw=b'{"sample":"cent"}'):
    return {
        "kind": "canonical_visible_readback_json",
        "filename": "plan7_single_item_discount_readback.json",
        "size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "content_b64": base64.b64encode(raw).decode(),
    }


def _read(*, present: bool):
    return {
        "ok": True,
        "web_agent_job_id": "post-read" if present else "pre-read",
        "scope_sha256": svc.SCOPE_SHA256,
        "activity_rows": _activity_rows(),
        "rows": [{
            **row,
            "classification": "correct_effective" if present else "missing",
            "actual_deduct": row["expected_deduct"] if present else None,
            "status": "进行中" if present else None,
            "activity_ids": [svc.TARGET_ACTIVITY_ID] if present else [],
        } for row in svc._expected_rows()],
        "platform_summary": {"correct_effective" if present else "missing": 4},
        "artifact": _artifact(),
        "execution_boundary": {"platform_write": False},
    }


def _prepare(db_session, monkeypatch):
    _install(db_session)
    monkeypatch.setattr(
        svc, "READONLY_ARTIFACT_SHA256", _artifact()["sha256"])
    monkeypatch.setattr(
        svc.campaign_discount_audit_service,
        "persist_single_discount_terminal",
        lambda **_kwargs: "terminal-receipt")


def _failed_attempt(db_session, monkeypatch):
    facts, error = svc._erp_scope(db_session)
    assert error is None
    attempt = CampaignExecutionAttempt(
        id=svc.RESUME_ATTEMPT_ID,
        plan_id=svc.PLAN_ID,
        workflow_key=svc.WORKFLOW_KEY,
        operation=svc.OPERATION,
        scope_sha256=svc.SCOPE_SHA256,
        state="failed",
        write_claimed=True,
        platform_write_observed=False,
        automatic_retry_allowed=False,
        request_id=svc.RESUME_ATTEMPT_REQUEST_ID,
        web_agent_job_id="job2",
        last_step="platform_outcome_not_exact",
        error_code="covered add-product action",
        result_summary={
            "trigger": {
                "workflow_key": svc.WORKFLOW_KEY,
                "plan_id": svc.PLAN_ID,
                "item_id": svc.ITEM_ID,
                "target_activity_id": svc.TARGET_ACTIVITY_ID,
                "scope_sha256": svc.SCOPE_SHA256,
                "readonly_artifact_sha256": svc.READONLY_ARTIFACT_SHA256,
                "pre_read_job_id": "job1",
            },
            "erp_facts": facts,
            "platform_submit": None,
            "official_terminal": None,
            "readback": None,
            "terminal_job_id": "job2",
            "terminal_error": "covered add-product action",
        },
    )
    db_session.add(attempt)
    db_session.commit()
    monkeypatch.setattr(
        svc, "RESUME_ATTEMPT_SNAPSHOT_SHA256",
        svc._attempt_snapshot_sha256(attempt))
    return attempt


def test_exact_four_skus_claim_once_and_read_back(db_session, monkeypatch):
    _prepare(db_session, monkeypatch)
    reads = iter([_read(present=False), _read(present=True)])
    monkeypatch.setattr(svc, "_platform_read", lambda *_: next(reads))
    writes = []

    def write(_db, *, payload):
        writes.append(payload)
        return {
            "ok": True,
            "submitted": True,
            "web_agent_job_id": "write-job",
            "trigger": {"activity_id": svc.TARGET_ACTIVITY_ID,
                        "action": "添加商品"},
            "platform_submit": {"attempted": True, "control": "确认修改"},
            "validation": {"ok": 4, "failed": 0},
            "official_terminal": {"state": "complete", "ok": 4, "failed": 0},
            "execution_boundary": {"platform_write": True},
        }

    monkeypatch.setattr(
        svc.web_agent_service,
        "supplement_plan7_sample_cent_single_discount", write)
    result = svc.execute_plan7_sample_cent(
        db_session, request_payload=svc.request_payload())

    assert result["ok"] is True
    assert result["activity_id"] == "143939511827"
    assert len(writes) == 1
    assert {row["expected_deduct"] for row in writes[0]["rows"]} == {"5.99"}
    assert not ({row["sku_id"] for row in writes[0]["rows"]}
                & svc.FORBIDDEN_SKU_IDS)
    attempt = db_session.query(CampaignExecutionAttempt).one()
    assert attempt.operation == svc.OPERATION
    assert attempt.state == "completed"
    assert attempt.write_claimed is True
    assert attempt.platform_write_observed is True
    assert attempt.automatic_retry_allowed is False
    assert all(row["pricing_daily_price"] == "30.00"
               for row in attempt.result_summary["erp_facts"])
    snapshot = db_session.query(CampaignEvidenceSnapshot).one()
    assert snapshot.evidence_type == "plan7_sample_cent_discount_readback"

    replay = svc.execute_plan7_sample_cent(
        db_session, request_payload=svc.request_payload())
    assert replay["error"] == "plan7_sample_cent_retired_after_success"
    assert len(writes) == 1


def test_identity_or_daily_price_drift_stops_before_platform(
        db_session, monkeypatch):
    _prepare(db_session, monkeypatch)
    price = db_session.query(PricingSku).first()
    price.daily_price = Decimal("29.99")
    db_session.commit()
    calls = []
    monkeypatch.setattr(svc, "_platform_read", lambda *_: calls.append(True))

    result = svc.execute_plan7_sample_cent(
        db_session, request_payload=svc.request_payload())

    assert result["error"] == "plan7_sample_cent_erp_identity_or_price_drift"
    assert calls == []
    assert db_session.query(CampaignExecutionAttempt).count() == 0


def test_failed_write_consumes_claim_and_never_retries(db_session, monkeypatch):
    _prepare(db_session, monkeypatch)
    monkeypatch.setattr(svc, "_platform_read", lambda *_: _read(present=False))
    writes = []

    def write(_db, *, payload):
        writes.append(payload)
        return {
            "ok": False,
            "submitted": True,
            "error": "partial",
            "official_terminal": {"state": "unknown", "ok": 3, "failed": 1},
            "execution_boundary": {"platform_write": True},
        }

    monkeypatch.setattr(
        svc.web_agent_service,
        "supplement_plan7_sample_cent_single_discount", write)
    first = svc.execute_plan7_sample_cent(
        db_session, request_payload=svc.request_payload())
    second = svc.execute_plan7_sample_cent(
        db_session, request_payload=svc.request_payload())

    assert first["error"] == "plan7_sample_cent_terminal_not_exact_no_retry"
    assert second["error"] == "plan7_sample_cent_attempt_already_claimed_no_retry"
    assert len(writes) == 1


def test_service_identity_and_cli_use_distinct_exact_path():
    assert dependencies.CAMPAIGN_PLAN7_SAMPLE_CENT_PATH in (
        dependencies.CAMPAIGN_PREPARE_SERVICE_PATHS)
    assert dependencies.CAMPAIGN_PLAN7_SAMPLE_CENT_PATH in cli._URL
    payload = svc.request_payload()
    assert payload["item_id"] == "719436834260"
    assert payload["target_activity_id"] == "143939511827"
    assert len(payload["rows"]) == 4


def test_v1_paused_status_artifact_is_retired():
    retired = dict(svc.request_payload())
    retired["readonly_artifact_sha256"] = (
        svc.RETIRED_V1_READONLY_ARTIFACT_SHA256)

    assert retired["readonly_artifact_sha256"] != svc.READONLY_ARTIFACT_SHA256
    assert svc._validate_request(retired) is False


def test_exact_failed_attempt_is_reused_once(db_session, monkeypatch):
    _prepare(db_session, monkeypatch)
    _failed_attempt(db_session, monkeypatch)
    reads = iter([_read(present=False), _read(present=True)])
    monkeypatch.setattr(svc, "_platform_read", lambda *_: next(reads))
    writes = []

    def write(_db, *, payload):
        writes.append(payload)
        return {
            "ok": True,
            "submitted": True,
            "web_agent_job_id": "resume-write-job",
            "trigger": {"activity_id": svc.TARGET_ACTIVITY_ID,
                        "action": "添加商品"},
            "platform_submit": {"attempted": True, "control": "确认修改"},
            "validation": {"ok": 4, "failed": 0},
            "official_terminal": {"state": "complete", "ok": 4,
                                  "failed": 0},
            "execution_boundary": {"platform_write": True},
        }

    monkeypatch.setattr(
        svc.web_agent_service,
        "supplement_plan7_sample_cent_single_discount", write)
    result = svc.resume_plan7_sample_cent(
        db_session, request_payload=svc.resume_request_payload())

    assert result["ok"] is True
    assert result["attempt_id"] == svc.RESUME_ATTEMPT_ID
    assert len(writes) == 1
    assert "attempt_id" not in writes[0]
    assert "confirmation" not in writes[0]
    assert db_session.query(CampaignExecutionAttempt).count() == 1
    attempt = db_session.get(CampaignExecutionAttempt, svc.RESUME_ATTEMPT_ID)
    assert attempt.state == "completed"
    assert attempt.platform_write_observed is True
    assert attempt.automatic_retry_allowed is False
    assert attempt.result_summary["resume"]["confirmation"] == (
        svc.RESUME_CONFIRMATION)

    replay = svc.resume_plan7_sample_cent(
        db_session, request_payload=svc.resume_request_payload())
    assert replay["error"] == "plan7_sample_cent_resume_attempt_state_drift"
    assert len(writes) == 1


def test_resume_attempt_drift_stops_before_platform(db_session, monkeypatch):
    _prepare(db_session, monkeypatch)
    attempt = _failed_attempt(db_session, monkeypatch)
    attempt.platform_write_observed = True
    db_session.commit()
    calls = []
    monkeypatch.setattr(svc, "_platform_read", lambda *_: calls.append(True))

    result = svc.resume_plan7_sample_cent(
        db_session, request_payload=svc.resume_request_payload())

    assert result["error"] == "plan7_sample_cent_resume_attempt_state_drift"
    assert calls == []


def test_resume_cli_and_payload_freeze_exact_attempt():
    payload = svc.resume_request_payload()
    assert payload["attempt_id"] == "a7280fed1f9d638c41b8f8ae"
    assert payload["confirmation"] == svc.RESUME_CONFIRMATION
    assert resume_cli._MAX_INPUT_BYTES >= 16_384
    assert svc._validate_resume_request(payload) is True
