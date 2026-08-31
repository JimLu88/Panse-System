from __future__ import annotations

import base64
from datetime import datetime
import hashlib
from io import BytesIO

import openpyxl
from sqlalchemy.orm import sessionmaker

from app.models.campaign import CampaignEvidenceSnapshot, CampaignPlan
from app.services import campaign_discount_audit_service as svc
from app.services import campaign_service, web_agent_service


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
        platform_active_until=datetime(2028, 7, 31, 23, 59, 59),
    )


def _scope() -> list[dict[str, str]]:
    return [{
        "item_id": str(100000000000 + (index % 55)),
        "sku_id": str(200000000000 + index),
        "expected_deduct": f"{index + 1:.2f}",
    } for index in range(392)]


def test_readonly_audit_persists_complete_rows_and_raw_artifact(
        db_session, monkeypatch):
    plan = _plan()
    db_session.add(plan)
    db_session.commit()
    scope = _scope()
    raw = b'{"platform":"visible-readback"}'
    evidence_rows = [{
        **row,
        "classification": "correct_effective",
        "actual_deduct": row["expected_deduct"],
        "status": "进行中",
        "activity_ids": ["142591608100"],
    } for row in scope]

    monkeypatch.setattr(svc, "_scope_rows", lambda *_: scope)
    monkeypatch.setattr(svc, "scope_sha256", lambda *_: svc.EXPECTED_SCOPE_SHA256)
    monkeypatch.setattr(
        svc.web_agent_service, "audit_plan7_single_discount",
        lambda *_args, **_kwargs: {
            "ok": True,
            "web_agent_job_id": "job9",
            "rows": evidence_rows,
            "platform_summary": {
                "expected_rows": 392,
                "classifications": {"correct_effective": 392},
            },
            "artifact": {
                "kind": "canonical_visible_readback_json",
                "filename": "readback.json",
                "size": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "content_b64": base64.b64encode(raw).decode(),
            },
        })

    result = svc.audit_plan7_single_discount(
        db_session,
        workflow_key=svc.WORKFLOW_KEY,
        expected_plan_id=7,
        expected_scope_sha256=svc.EXPECTED_SCOPE_SHA256,
    )

    assert result["ok"] is True
    assert result["execution_boundary"]["platform_write"] is False
    saved = db_session.query(CampaignEvidenceSnapshot).one()
    assert saved.web_agent_job_id == "job9"
    assert len(saved.rows) == 392
    assert saved.artifact_blob == raw
    assert saved.artifact_sha256 == hashlib.sha256(raw).hexdigest()


def test_audit_rejects_incomplete_platform_scope(db_session, monkeypatch):
    db_session.add(_plan())
    db_session.commit()
    scope = _scope()
    monkeypatch.setattr(svc, "_scope_rows", lambda *_: scope)
    monkeypatch.setattr(svc, "scope_sha256", lambda *_: svc.EXPECTED_SCOPE_SHA256)
    monkeypatch.setattr(
        svc.web_agent_service, "audit_plan7_single_discount",
        lambda *_args, **_kwargs: {
            "ok": True,
            "rows": [{**scope[0], "classification": "missing"}],
            "artifact": {},
        })

    result = svc.audit_plan7_single_discount(
        db_session,
        workflow_key=svc.WORKFLOW_KEY,
        expected_plan_id=7,
        expected_scope_sha256=svc.EXPECTED_SCOPE_SHA256,
    )

    assert result["ok"] is False
    assert result["error"] == "plan7_discount_audit_incomplete_platform_result"
    assert db_session.query(CampaignEvidenceSnapshot).count() == 0


def test_wrong_fixed_identity_never_contacts_web_agent(db_session, monkeypatch):
    calls = []
    monkeypatch.setattr(
        svc.web_agent_service, "audit_plan7_single_discount",
        lambda *_args, **_kwargs: calls.append(True))
    result = svc.audit_plan7_single_discount(
        db_session, workflow_key="campaign:super88:49462:49469",
        expected_plan_id=8, expected_scope_sha256=svc.EXPECTED_SCOPE_SHA256)
    assert result["ok"] is False
    assert result["execution_boundary"]["platform_read"] is False
    assert calls == []


def test_future_partial_terminal_survives_with_target_and_failure_artifacts(
        db_session, monkeypatch):
    db_session.add(_plan())
    db_session.commit()
    Session = sessionmaker(bind=db_session.get_bind(), future=True)
    monkeypatch.setattr(svc, "SessionLocal", Session)
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["商品id", "SKU_ID", "优惠值"])
    sheet.append(["100000001", "200000001", 12.34])
    output = BytesIO()
    workbook.save(output)
    target = output.getvalue()
    failed = b"PK-failure-workbook"
    result = {
        "submitted": True,
        "validation": {"ok": 387, "failed": 1},
        "final_import": {
            "ok": 387, "failed": 1, "state": "partial",
            "failed_rows": [{"item_id": "100", "sku_id": "200", "reason": "失败"}],
            "failed_artifact": {
                "filename": "failed.xlsx",
                "xlsx_size": len(failed),
                "sha256": hashlib.sha256(failed).hexdigest(),
                "xlsx_b64": base64.b64encode(failed).decode(),
            },
        },
    }

    request_id = svc.persist_single_discount_terminal(
        plan_id=7, workflow_key=svc.WORKFLOW_KEY, job_id="job10",
        target_xlsx=target, result=result)

    assert request_id
    db_session.expire_all()
    saved = db_session.query(CampaignEvidenceSnapshot).one()
    assert saved.result_status == "partial"
    assert saved.artifact_blob == target
    assert saved.scope_sha256 == svc.xlsx_scope_sha256(target)
    assert saved.failure_artifact_blob == failed
    assert saved.failure_rows[0]["reason"] == "失败"
    assert saved.execution_boundary["automatic_retry"] is False


def test_commit_wait_records_terminal_before_return(db_session, monkeypatch):
    plan = _plan()
    captured = {}
    monkeypatch.setattr(
        web_agent_service, "upload_file",
        lambda *_args, **_kwargs: {"ok": True, "job": "job11"})
    monkeypatch.setattr(
        web_agent_service, "wait_job",
        lambda *_args, **_kwargs: {"result": {
            "ok": False, "submitted": True,
            "validation": {"ok": 2, "failed": 1},
            "final_import": {"ok": 2, "failed": 1, "state": "partial"},
        }})

    def persist(**kwargs):
        captured.update(kwargs)
        return "terminal-receipt-1"

    monkeypatch.setattr(svc, "persist_single_discount_terminal", persist)
    result = campaign_service._upload_and_wait(
        db_session, "single_item_discount", "commit", b"PK-target",
        "2026-09-01 00:00:00", "2026-09-01 23:59:59",
        plan=plan, expected_rows=3)
    assert result["evidence_request_id"] == "terminal-receipt-1"
    assert captured["job_id"] == "job11"
    assert captured["target_xlsx"] == b"PK-target"
    assert result["ok"] is False
    assert result["submitted"] is True
    assert "未完整成功" in result["error"]


def test_commit_wait_fails_closed_when_terminal_receipt_cannot_persist(
        db_session, monkeypatch):
    plan = _plan()
    monkeypatch.setattr(
        web_agent_service, "upload_file",
        lambda *_args, **_kwargs: {"ok": True, "job": "job12"})
    monkeypatch.setattr(
        web_agent_service, "wait_job",
        lambda *_args, **_kwargs: {"result": {
            "ok": True, "submitted": True,
            "validation": {"ok": 3, "failed": 0},
            "final_import": {"ok": 3, "failed": 0, "state": "complete"},
        }})
    monkeypatch.setattr(
        svc, "persist_single_discount_terminal",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("db unavailable")))
    result = campaign_service._upload_and_wait(
        db_session, "single_item_discount", "commit", b"PK-target",
        "2026-09-01 00:00:00", "2026-09-01 23:59:59",
        plan=plan, expected_rows=3)
    assert result["ok"] is False
    assert result["submitted"] is True
    assert "禁止自动重试" in result["error"]
