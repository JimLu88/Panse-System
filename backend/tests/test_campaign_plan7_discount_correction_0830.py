import base64
from datetime import datetime
import hashlib

import openpyxl

from app.models.campaign import CampaignEvidenceSnapshot, CampaignPlan
from app.services import campaign_discount_correction_service as svc
from app.services import settings_service


def _plan() -> CampaignPlan:
    return CampaignPlan(
        id=7,
        workflow_key=svc.WORKFLOW_KEY,
        name="2026-09-01超级立减更新窗口",
        campaign_type="super_reduce",
        tier="mid",
        start_at=datetime(2026, 9, 1, 0, 0, 0),
        end_at=datetime(2026, 9, 1, 23, 59, 59),
        qn_campaign_title="超级立减",
        status="alarmed",
        remark="official_all_store=true; official_exempt_items=805268708396",
        platform_activity_mode="long_running_update",
    )


def _missing_rows():
    return [{
        "item_id": row["item_id"],
        "sku_id": row["sku_id"],
        "expected_deduct": row["deduct"],
        "actual_deduct": None,
        "classification": "missing",
        "status": None,
        "activity_ids": [],
    } for row in svc.EXPECTED_ROWS]


def _present_rows():
    return [{
        "item_id": row["item_id"],
        "sku_id": row["sku_id"],
        "expected_deduct": row["deduct"],
        "actual_deduct": row["deduct"],
        "classification": "present_not_effective",
        "status": "未开始",
        "activity_ids": ["corrected-activity"],
    } for row in svc.EXPECTED_ROWS]


def _snapshot_rows():
    present = [{
        "item_id": str(1000000000000 + index),
        "sku_id": str(2000000000000 + index),
        "expected_deduct": "1.00",
        "actual_deduct": "1.00",
        "classification": "present_not_effective",
        "status": "未开始",
        "activity_ids": [svc.EXPECTED_ACTIVITY_ID],
    } for index in range(384)]
    return present + _missing_rows()


def _raw_build_rows():
    return [{
        "taobao_item_id": row["item_id"],
        "taobao_sku_id": row["sku_id"],
        "sku_code": row["sku_code"],
        "deduct": float(row["deduct"]),
        "official": float(row["official"]),
        "target_price": float(row["final"]),
        "calculation_base": float(row["daily"]),
        "kind": "nosales",
        "concession": 0.0,
    } for row in svc.EXPECTED_ROWS]


def _install_snapshot(db_session, monkeypatch):
    raw = b'{"immutable":"snapshot"}'
    sha = hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(svc, "EXPECTED_SNAPSHOT_ARTIFACT_SHA256", sha)
    db_session.add(_plan())
    db_session.add(CampaignEvidenceSnapshot(
        id=1,
        plan_id=7,
        workflow_key=svc.WORKFLOW_KEY,
        evidence_type="single_item_discount_readback",
        request_id=svc.EXPECTED_SNAPSHOT_REQUEST_ID,
        scope_sha256=svc.EXPECTED_FULL_SCOPE_SHA256,
        result_status="differences",
        rows=_snapshot_rows(),
        failure_rows=[],
        execution_boundary={"platform_write": False},
        artifact_sha256=sha,
        artifact_size=len(raw),
        artifact_blob=raw,
    ))
    db_session.commit()
    monkeypatch.setattr(
        svc, "_now_shanghai", lambda: datetime(2026, 8, 30, 12, 0, 0))
    monkeypatch.setattr(
        svc.campaign_service, "build_discount_rows",
        lambda *_args, **_kwargs: (_raw_build_rows(), {}))
    return sha


def _artifact():
    raw = b'{"four":"rows"}'
    return {
        "kind": "canonical_visible_readback_json",
        "filename": "plan7-four-row-readback.json",
        "size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "content_b64": base64.b64encode(raw).decode(),
    }


def _call(db_session, sha):
    return svc.correct_plan7_single_discount(
        db_session,
        workflow_key=svc.WORKFLOW_KEY,
        expected_plan_id=7,
        expected_snapshot_id=1,
        expected_snapshot_artifact_sha256=sha,
        expected_missing_scope_sha256=svc.EXPECTED_MISSING_SCOPE_SHA256,
    )


def test_exact_four_row_correction_submits_once_and_reads_back(
        db_session, monkeypatch):
    sha = _install_snapshot(db_session, monkeypatch)
    reads = iter([
        {"ok": True, "web_agent_job_id": "job-read-before", "rows": _missing_rows()},
        {
            "ok": True,
            "web_agent_job_id": "job-read-after",
            "rows": _present_rows(),
            "platform_summary": {"present_not_effective": 4},
            "artifact": _artifact(),
        },
    ])
    monkeypatch.setattr(svc, "_platform_read", lambda *_: next(reads))
    captured = {}

    def upload(_db, channel, phase, xlsx, start, end, **kwargs):
        captured.update({
            "channel": channel, "phase": phase, "xlsx": xlsx,
            "start": start, "end": end, **kwargs,
        })
        return {
            "ok": True, "submitted": True, "job": "job-write",
            "evidence_request_id": "terminal-four-rows",
            "final_import": {"state": "complete", "ok": 4, "failed": 0},
        }

    monkeypatch.setattr(svc.campaign_service, "_upload_and_wait", upload)
    result = _call(db_session, sha)

    assert result["ok"] is True
    assert result["attempt"]["status"] == "completed"
    assert result["execution_boundary"]["platform_write"] is True
    assert result["execution_boundary"]["touches_existing_384_rows"] is False
    assert captured["channel"] == "single_item_discount"
    assert captured["phase"] == "commit"
    assert captured["expected_rows"] == 4
    assert captured["ignore_plan_discount_activity"] is True
    assert svc.campaign_discount_audit_service.xlsx_scope_sha256(
        captured["xlsx"]) == svc.EXPECTED_MISSING_SCOPE_SHA256
    workbook = openpyxl.load_workbook(
        __import__("io").BytesIO(captured["xlsx"]), read_only=True)
    try:
        values = list(workbook.active.iter_rows(min_row=2, values_only=True))
    finally:
        workbook.close()
    assert len(values) == 4
    assert {str(row[0]) for row in values} == {svc.EXPECTED_ITEM_ID}
    assert {str(row[1]) for row in values} == {
        row["sku_id"] for row in svc.EXPECTED_ROWS}
    saved = db_session.query(CampaignEvidenceSnapshot).order_by(
        CampaignEvidenceSnapshot.id).all()
    assert len(saved) == 2
    assert saved[-1].evidence_type == "single_item_discount_correction_readback"
    assert saved[-1].artifact_blob == b'{"four":"rows"}'


def test_exact_rows_already_present_is_noop(db_session, monkeypatch):
    sha = _install_snapshot(db_session, monkeypatch)
    monkeypatch.setattr(svc, "_platform_read", lambda *_: {
        "ok": True,
        "web_agent_job_id": "job-read-only",
        "rows": _present_rows(),
    })
    writes = []
    monkeypatch.setattr(
        svc.campaign_service, "_upload_and_wait",
        lambda *_args, **_kwargs: writes.append(True))

    result = _call(db_session, sha)

    assert result["ok"] is True
    assert result["already_exact_no_write"] is True
    assert result["execution_boundary"]["platform_write"] is False
    assert writes == []


def test_terminal_failure_is_claimed_and_never_retried(db_session, monkeypatch):
    sha = _install_snapshot(db_session, monkeypatch)
    reads = []

    def read(*_args):
        reads.append(True)
        return {"ok": True, "web_agent_job_id": "job-read", "rows": _missing_rows()}

    monkeypatch.setattr(svc, "_platform_read", read)
    writes = []

    def upload(*_args, **_kwargs):
        writes.append(True)
        return {
            "ok": False, "submitted": True, "job": "job-partial",
            "error": "partial", "evidence_request_id": "terminal-partial",
        }

    monkeypatch.setattr(svc.campaign_service, "_upload_and_wait", upload)
    first = _call(db_session, sha)
    second = _call(db_session, sha)

    assert first["ok"] is False
    assert first["error"] == "discount_correction_terminal_failed_no_retry"
    assert second["error"] == "discount_correction_attempt_already_claimed_no_retry"
    assert len(reads) == 1
    assert len(writes) == 1


def test_snapshot_drift_stops_before_platform(db_session, monkeypatch):
    sha = _install_snapshot(db_session, monkeypatch)
    snapshot = db_session.get(CampaignEvidenceSnapshot, 1)
    changed = [dict(row) for row in snapshot.rows]
    changed[0]["actual_deduct"] = "9.99"
    snapshot.rows = changed
    db_session.commit()
    reads = []
    monkeypatch.setattr(svc, "_platform_read", lambda *_: reads.append(True))

    result = _call(db_session, sha)

    assert result["ok"] is False
    assert result["error"] == "discount_correction_snapshot_rows_mismatch"
    assert reads == []


def test_pre_submit_read_failure_does_not_claim_business_attempt(
        db_session, monkeypatch):
    sha = _install_snapshot(db_session, monkeypatch)
    monkeypatch.setattr(svc, "_platform_read", lambda *_: {
        "ok": False, "error": "login_required", "web_agent_job_id": "job-login",
    })

    result = _call(db_session, sha)

    assert result["error"] == "login_required"
    assert settings_service.get(
        db_session, svc.ATTEMPT_KEY, env_fallback=False) is None
    assert result["execution_boundary"]["platform_write"] is False
