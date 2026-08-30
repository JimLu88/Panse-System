"""Plan-7 remaining signup: exact scope, batching, readback and no retry."""
from __future__ import annotations

from datetime import datetime

from app.models.campaign import CampaignEvidenceSnapshot, CampaignPlan
from app.services import (
    campaign_plan7_remaining_signup_service as service,
    campaign_service,
    web_agent_service,
)


def _plan() -> CampaignPlan:
    return CampaignPlan(
        id=7,
        workflow_key=service.WORKFLOW_KEY,
        name="2026-09-01超级立减更新窗口",
        campaign_type="super_reduce",
        tier="mid",
        start_at=datetime(2026, 9, 1, 0, 0, 0),
        end_at=datetime(2026, 9, 1, 23, 59, 59),
        qn_campaign_title="超级立减",
        status="alarmed",
        remark=(
            "platform_qualified_items=797294092429; "
            "platform_no_sales_items=; platform_hard_failed_items=; "
            "official_all_store=true; official_exempt_items=805268708396"
        ),
        platform_activity_mode="long_running_update",
    )


def _rows():
    rows = []
    global_index = 0
    item_ids = sorted(service.AUTHORIZED_PLACEHOLDER_ITEM_IDS)
    missing_counts = {
        "1036273574687": (13, 4),
        "1074244132390": (16, 4),
        "717809819543": (11, 4),
        "793084818113": (18, 2),
        "793202812082": (12, 4),
    }
    other_index = 0
    for item_id in item_ids:
        if item_id in missing_counts:
            item_row_count, placeholder_count = missing_counts[item_id]
        else:
            # The other 25 reviewed items total 229 rows. Their exact production
            # distribution is covered by the immutable live-scope constants;
            # this fixture only needs stable per-item completeness semantics.
            item_row_count = 10 if other_index < 4 else 9
            placeholder_count = 0
            other_index += 1
        for row_index in range(item_row_count):
            global_index += 1
            is_placeholder = row_index < placeholder_count
            rows.append({
                "taobao_item_id": item_id,
                "taobao_sku_id": f"9{global_index:012d}",
                "sku_code": f"SKU{global_index:03d}",
                "price": float(100 + global_index),
                "is_placeholder": is_placeholder,
                "remark": (
                    "用户已授权定制咨询规格使用保护报名价"
                    if is_placeholder else None
                ),
            })
    return rows


def _request():
    return {
        "workflow_key": service.WORKFLOW_KEY,
        "expected_plan_id": 7,
        "expected_status": "alarmed",
        "expected_item_scope_sha256": service.AUTHORIZED_ITEM_SCOPE_SHA256,
        "recovery_incident_id": service.RECOVERY_INCIDENT_ID,
    }


def test_stale_full_scope_recovery_payload_is_rejected_before_any_read(
        db_session, monkeypatch):
    called = []
    monkeypatch.setattr(
        campaign_service, "refresh_floor_evidence_from_current_activity",
        lambda *_args, **_kwargs: called.append(True),
    )
    stale = _request()
    stale.update({
        "expected_item_scope_sha256": service.AUTHORIZED_FULL_SCOPE_SHA256,
        "recovery_incident_id": "plan7-preclaim-export-e222849772c5",
    })

    result = service.execute_plan7_remaining_signup(db_session, **stale)

    assert result["ok"] is False
    assert result["error"] == "remaining_signup_request_not_allowed"
    assert result["execution_boundary"]["platform_write"] is False
    assert called == []


def _patch_scope(monkeypatch, *, terminal_failure=False):
    rows = _rows()
    missing_rows = [
        row for row in rows
        if row["taobao_item_id"] in service.AUTHORIZED_MISSING_ITEM_IDS
    ]
    blocked = [{"taobao_item_id": item_id} for item_id in sorted(
        service.AUTHORIZED_PLACEHOLDER_ITEM_IDS)]
    no_sales = sorted(service.NO_SALES_ITEM_IDS)
    whole_exclusions = [{
        "item_id": item_id,
        "reason": "all_mapped_skus_authoritatively_marked_custom_placeholder",
    } for item_id in sorted(service.WHOLE_ITEM_EXCLUSION_IDS)]

    def fake_build(_db, _plan, **kwargs):
        if kwargs.get("allow_placeholder_safe_lowering"):
            return rows, {
                "excluded_no_sales_items": no_sales,
                "excluded_price_hold_items": [
                    {"taobao_item_id": item_id}
                    for item_id in sorted(service.PRICE_HOLD_ITEM_IDS)
                ],
                "excluded_official_exempt_items": sorted(
                    service.OFFICIAL_EXEMPT_ITEM_IDS),
                "placeholder_price_lowered": [],
                "excluded_whole_items": whole_exclusions,
            }
        return [], {
            "placeholder_price_blocked_items": blocked,
            "excluded_no_sales_items": no_sales,
            "excluded_price_hold_items": [
                {"taobao_item_id": item_id}
                for item_id in sorted(service.PRICE_HOLD_ITEM_IDS)
            ],
            "excluded_official_exempt_items": sorted(
                service.OFFICIAL_EXEMPT_ITEM_IDS),
            "excluded_whole_items": whole_exclusions,
        }

    refresh_calls = []

    def platform_row(row, *, conflict=False):
        return {
            "item_id": row["taobao_item_id"],
            "sku_id": row["taobao_sku_id"],
            "activity_price": row["price"] + (1 if conflict else 0),
            "status": "暂停",
        }

    reviewed_live_rows = []
    for item_id in sorted(
            service.READONLY_QUALIFIED_ITEM_IDS
            | service.READONLY_HARD_STOP_ITEM_IDS):
        item_rows = [row for row in rows if row["taobao_item_id"] == item_id]
        for index, row in enumerate(item_rows):
            reviewed_live_rows.append(platform_row(
                row,
                conflict=(item_id in service.READONLY_HARD_STOP_ITEM_IDS
                          and index == 0),
            ))

    def fake_refresh(*_args, **_kwargs):
        refresh_calls.append(True)
        if len(refresh_calls) == 1:
            return {"ok": True, "rows": reviewed_live_rows,
                    "export_evidence": {"sha256": "before", "job_id": "read1"}}
        accepted_rows = missing_rows[:-1] if terminal_failure else missing_rows
        return {
            "ok": True,
            "rows": reviewed_live_rows + [
                platform_row(row) for row in accepted_rows],
            "export_evidence": {
                "filename": "超级立减已报商品列表.xlsx",
                "sha256": "after",
                "size": 123,
                "job_id": "read2",
            },
        }

    monkeypatch.setattr(campaign_service, "build_signup_rows", fake_build)
    monkeypatch.setattr(
        campaign_service, "refresh_floor_evidence_from_current_activity",
        fake_refresh)
    monkeypatch.setattr(
        campaign_service, "official_scope_for_plan",
        lambda _plan: {"configured": True, "all_store": True,
                       "exempt_items": set(service.OFFICIAL_EXEMPT_ITEM_IDS)})
    monkeypatch.setattr(
        campaign_service, "platform_qualified_items",
        lambda _plan: set(service.ACCEPTED_ITEM_IDS))
    monkeypatch.setattr(
        service, "_validate_price_sources",
        lambda _db, current_rows, _stats: {
            "ok": True,
            "real_sku_rows": sum(not row["is_placeholder"] for row in current_rows),
            "placeholder_sku_rows": sum(row["is_placeholder"] for row in current_rows),
            "problems": [],
        })
    monkeypatch.setattr(
        campaign_service, "build_discount_rows",
        lambda *_args, **_kwargs: ([], {"rows": 0}))
    monkeypatch.setattr(
        campaign_service, "_check_price_math",
        lambda *_args, **_kwargs: {
            "rule": "R13", "level": "pass", "items": []})
    monkeypatch.setattr(
        campaign_service, "preflight",
        lambda *_args, **_kwargs: [
            {"rule": "R16", "level": "pass"},
            {"rule": "R17", "level": "pass",
             "checked": sum(not row["is_placeholder"] for row in missing_rows)},
        ])
    monkeypatch.setattr(
        campaign_service, "_build_super_signup_xlsx", lambda _rows: b"xlsx")
    upload_calls = []

    def fake_upload(*_args, **_kwargs):
        upload_calls.append(True)
        return {
            "ok": not terminal_failure,
            "submitted": True,
            "job": "job1",
            "validation": {
                "total_items": 5,
                "ok": 4 if terminal_failure else 5,
                "failed": 1 if terminal_failure else 0,
            },
        }

    monkeypatch.setattr(campaign_service, "_upload_and_wait", fake_upload)

    def fake_classify(_db, _plan, _result, _pending, correct_items):
        accepted = set(service.AUTHORIZED_MISSING_ITEM_IDS)
        hard = set()
        if terminal_failure:
            hard = {sorted(accepted)[-1]}
            accepted -= hard
        return {
            "ok": True,
            "accepted_item_ids": sorted(accepted),
            "qualified_item_ids": sorted(set(correct_items) | accepted),
            "no_sales_item_ids": [],
            "hard_failed_item_ids": sorted(hard),
        }

    monkeypatch.setattr(
        campaign_service, "_classify_final_signup", fake_classify)
    monkeypatch.setattr(
        campaign_service, "_record_signup_execution_receipt",
        lambda *_args, **_kwargs: {})
    return missing_rows, upload_calls


def test_remaining_signup_claims_once_and_requires_all_sku_readback(
        db_session, monkeypatch):
    plan = _plan()
    db_session.add(plan)
    db_session.commit()
    rows, uploads = _patch_scope(monkeypatch)

    first = service.execute_plan7_remaining_signup(db_session, **_request())
    replay = service.execute_plan7_remaining_signup(db_session, **_request())

    assert first["ok"] is True
    assert first["item_count"] == 5
    assert first["row_count"] == len(rows)
    assert first["attempt"]["status"] == "completed"
    assert first["attempt"]["batches"][0]["post_submit_verification"]["checked_skus"] == len(rows)
    assert first["execution_boundary"]["single_item_discount_write"] is False
    assert replay["ok"] is True and replay["idempotent_replay"] is True
    assert uploads == [True]
    assert plan.status == "alarmed"
    assert first["attempt"]["readonly_qualified_item_ids"] == sorted(
        service.READONLY_QUALIFIED_ITEM_IDS)
    assert first["attempt"]["readonly_hard_stop_item_ids"] == sorted(
        service.READONLY_HARD_STOP_ITEM_IDS)
    review = db_session.query(CampaignEvidenceSnapshot).filter_by(
        plan_id=7, evidence_type="plan7_remaining_scope_review").one()
    assert review.platform_summary["attempt_claimed"] is False
    assert review.execution_boundary["platform_write"] is False


def test_remaining_signup_failed_batch_is_not_retried(db_session, monkeypatch):
    plan = _plan()
    db_session.add(plan)
    db_session.commit()
    _, uploads = _patch_scope(monkeypatch, terminal_failure=True)

    failed = service.execute_plan7_remaining_signup(db_session, **_request())
    replay = service.execute_plan7_remaining_signup(db_session, **_request())

    assert failed["ok"] is False
    assert failed["error"] == "remaining_signup_batch_failed_no_retry"
    assert failed["attempt"]["status"] == "failed_no_retry"
    assert replay["error"] == "remaining_signup_attempt_already_claimed_no_retry"
    assert uploads == [True]
    assert plan.status == "alarmed"


def test_preclaim_read_failure_is_safe_to_recover(db_session, monkeypatch):
    plan = _plan()
    db_session.add(plan)
    db_session.commit()
    monkeypatch.setattr(
        campaign_service, "official_scope_for_plan",
        lambda _plan: {"configured": True, "all_store": True,
                       "exempt_items": set(service.OFFICIAL_EXEMPT_ITEM_IDS)})
    monkeypatch.setattr(
        campaign_service, "platform_qualified_items",
        lambda _plan: set(service.ACCEPTED_ITEM_IDS))
    monkeypatch.setattr(
        campaign_service, "refresh_floor_evidence_from_current_activity",
        lambda *_args, **_kwargs: {
            "ok": False,
            "error": "订单取数正在运行，本次淘宝营销/评价任务已让路；请稍后自动重试。",
            "step": "web_agent_job_terminal",
            "job_id": "job2",
            "detail": {"job_status": "error"},
        })

    failed = service.execute_plan7_remaining_signup(db_session, **_request())

    assert failed["ok"] is False
    assert failed["step"] == "web_agent_job_terminal"
    assert failed["execution_boundary"]["platform_write"] is False
    assert service._load_attempt(db_session) is None
    assert plan.status == "alarmed"
    snapshots = db_session.query(CampaignEvidenceSnapshot).filter_by(
        plan_id=7, evidence_type="plan7_remaining_preclaim").all()
    assert len(snapshots) == 1
    assert snapshots[0].result_status == "preclaim_failed"
    assert snapshots[0].platform_summary["attempt_claimed"] is False
    assert snapshots[0].execution_boundary["platform_write"] is False


def test_batch_builder_keeps_each_item_whole_and_splits_at_item_limit():
    rows = [{
        "taobao_item_id": f"8{index:011d}",
        "taobao_sku_id": f"9{index:011d}",
        "sku_code": f"S{index}",
        "price": 100.0,
        "is_placeholder": False,
    } for index in range(51)]

    batches = service._build_batches(rows)

    assert [len(batch["item_ids"]) for batch in batches] == [50, 1]
    assert sum(batch["row_count"] for batch in batches) == 51


def test_partial_import_semantics_are_preserved_as_platform_draft_write(
        db_session, monkeypatch):
    plan = _plan()
    monkeypatch.setattr(
        web_agent_service, "upload_file",
        lambda *_args, **_kwargs: {"ok": True, "job": "job4"})
    monkeypatch.setattr(
        web_agent_service, "wait_job",
        lambda *_args, **_kwargs: {
            "result": {
                "ok": False,
                "attached": True,
                "platform_write_observed": True,
                "published": False,
                "stopped_before": "一键发布",
                "operation_semantics": "file_import_writes_platform_draft",
                "mode": "batch_import",
                "validation": {
                    "total_items": 5, "ok": 2, "failed": 3,
                },
                "error": "super_reduce_import_failed_items",
            },
        })

    result = campaign_service._upload_and_wait(
        db_session, "super_reduce", "commit", b"xlsx", None, None,
        plan=plan, expected_rows=70, expected_items=5)

    assert result["ok"] is False
    assert result["submitted"] is None
    assert result["platform_write_observed"] is True
    assert result["published"] is False
    assert result["stopped_before"] == "一键发布"
    assert result["validation"] == {"total_items": 5, "ok": 2, "failed": 3}


def test_partial_enrollment_classifier_downloads_feedback_without_markers(
        db_session, monkeypatch):
    plan = _plan()
    original_remark = plan.remark
    pending = [{
        "taobao_item_id": str(index),
        "taobao_sku_id": f"9{index}",
        "price": 100.0,
        "is_placeholder": False,
    } for index in range(1, 6)]
    failed = [{
        "item_id": str(index),
        "sku_id": f"9{index}",
        "reason": "其他规则",
        "raw": "官方失败",
    } for index in range(3, 6)]
    monkeypatch.setattr(
        web_agent_service, "super_reduce_feedback",
        lambda *_args, **_kwargs: {
            "ok": True,
            "feedback": {"failed": failed, "by_reason": []},
            "filename": "官方失败表.xlsx",
            "xlsx_bytes": b"PK-feedback",
        })

    result = campaign_service._classify_final_signup(
        db_session, plan, {
            "submitted": False,
            "attached": True,
            "platform_write_observed": True,
            "stopped_before": "一键发布",
            "validation": {
                "total_items": 5, "ok": 2, "failed": 3,
                "failed_items": [], "failed_reasons": [],
            },
        }, pending, set())

    assert result["ok"] is False
    assert result["error"] == "signup_partial_enrollment_import_paused"
    assert result["platform_write_observed"] is True
    assert result["accepted_item_ids"] == ["1", "2"]
    assert result["enrolled_paused_item_ids"] == ["1", "2"]
    assert result["draft_imported_item_ids"] == []
    assert result["failed_item_ids"] == ["3", "4", "5"]
    assert plan.remark == original_remark


def test_partial_signup_audit_persists_official_failure_artifact_once(
        db_session, monkeypatch):
    plan = _plan()
    db_session.add(plan)
    db_session.commit()
    source_rows = [
        row for row in _rows()
        if row["taobao_item_id"] in service.AUTHORIZED_MISSING_ITEM_IDS
    ]
    digest = service._manifest_digest(source_rows)
    monkeypatch.setattr(service, "PARTIAL_MANIFEST_SHA256", digest)
    manifest = service._manifest_rows(source_rows)
    service._save_attempt(db_session, {
        "attempt_id": service.PARTIAL_ATTEMPT_ID,
        "manifest_sha256": digest,
        "status": "failed_no_retry",
        "manifest_rows": manifest,
        "batches": [{
            "status": "failed_no_retry",
            "terminal_validation": {
                "total_items": 5, "ok": 2, "failed": 3,
            },
        }],
    })
    db_session.commit()
    failed_ids = sorted(service.AUTHORIZED_MISSING_ITEM_IDS)[-3:]
    failed_rows = []
    for item_id in failed_ids:
        item_row = next(
            row for row in source_rows if row["taobao_item_id"] == item_id)
        failed_rows.append({
            "item_id": item_id,
            "sku_id": item_row["taobao_sku_id"],
            "reason": "整品规则不通过",
            "raw": "官方失败原因",
        })
    calls = []
    feedback_bytes = b"PK" + b"x" * 200
    monkeypatch.setattr(
        web_agent_service, "super_reduce_feedback",
        lambda *_args, **_kwargs: calls.append("feedback") or {
            "ok": True, "job_id": "job-audit",
            "filename": "官方失败表.xlsx", "xlsx_bytes": feedback_bytes,
            "feedback": {
                "failed": failed_rows,
                "by_reason": [{"reason": "整品规则不通过", "items": 3}],
            },
        })
    monkeypatch.setattr(
        campaign_service, "refresh_floor_evidence_from_current_activity",
        lambda *_args, **_kwargs: calls.append("export") or {
            "ok": True, "rows": [],
            "export_evidence": {
                "filename": "超级立减已报商品列表.xlsx",
                "sha256": "a" * 64, "size": 123, "job_id": "job-export",
            },
        })
    monkeypatch.setattr(
        campaign_service, "build_discount_rows",
        lambda *_args, **_kwargs: ([], {}))
    monkeypatch.setattr(
        campaign_service, "_check_price_math",
        lambda *_args, **_kwargs: {"rule": "R13", "level": "pass"})
    request = {
        "workflow_key": service.WORKFLOW_KEY,
        "expected_plan_id": 7,
        "expected_attempt_id": service.PARTIAL_ATTEMPT_ID,
        "expected_manifest_sha256": digest,
    }

    first = service.audit_plan7_partial_signup(db_session, **request)
    replay = service.audit_plan7_partial_signup(db_session, **request)

    assert first["ok"] is True
    assert first["published"] is None
    assert first["platform_write_kind"] == "partial_enrollment_import"
    assert len(first["accepted_item_ids"]) == 2
    assert first["enrolled_paused_item_ids"] == []
    assert first["draft_imported_item_ids"] == []
    assert first["failed_item_ids"] == failed_ids
    assert first["official_active_item_ids"] == []
    assert first["safe_failed_only_recovery_available"] is False
    assert replay["ok"] is True and replay["idempotent_replay"] is True
    assert calls == ["feedback", "export"]
    snapshot = db_session.query(CampaignEvidenceSnapshot).filter_by(
        evidence_type=service.PARTIAL_AUDIT_EVIDENCE_TYPE).one()
    assert snapshot.failure_artifact_blob == feedback_bytes
    assert snapshot.execution_boundary["platform_write"] is False


def _draft_publish_snapshot(db_session, monkeypatch):
    verified = {
        "717809819543": [{
            "item_id": "717809819543", "sku_id": "s1",
            "expected_activity_price": 100.0,
            "is_custom_placeholder": False, "statuses": ["暂停"],
        }, {
            "item_id": "717809819543", "sku_id": "s2",
            "expected_activity_price": 50.0,
            "is_custom_placeholder": True, "statuses": ["暂停"],
        }],
        "793084818113": [{
            "item_id": "793084818113", "sku_id": "s3",
            "expected_activity_price": 200.0,
            "is_custom_placeholder": False, "statuses": ["暂停"],
        }, {
            "item_id": "793084818113", "sku_id": "s4",
            "expected_activity_price": 60.0,
            "is_custom_placeholder": True, "statuses": ["暂停"],
        }],
    }
    summary = {
        "attempt_id": service.PARTIAL_ATTEMPT_ID,
        "draft_imported_item_ids": sorted(service.DRAFT_PUBLISH_ITEM_IDS),
        "failed_item_ids": sorted(
            service.AUTHORIZED_MISSING_ITEM_IDS
            - service.DRAFT_PUBLISH_ITEM_IDS),
        "official_paused_or_pending_item_ids": sorted(
            service.DRAFT_PUBLISH_ITEM_IDS),
        "published": False,
        "per_item_sku_verification": {
            item_id: {"ok": True, "verified": rows}
            for item_id, rows in verified.items()
        },
    }
    rows = service._draft_publish_rows(summary)
    monkeypatch.setattr(service, "DRAFT_PUBLISH_SKU_COUNT", len(rows))
    monkeypatch.setattr(
        service, "DRAFT_PUBLISH_SCOPE_SHA256",
        service._draft_publish_scope_sha256(rows))
    snapshot = CampaignEvidenceSnapshot(
        plan_id=7, workflow_key=service.WORKFLOW_KEY,
        evidence_type=service.PARTIAL_AUDIT_EVIDENCE_TYPE,
        request_id="draft-publish-source", scope_sha256="a" * 64,
        result_status="partial_draft_import_audited",
        platform_summary=summary, rows=[], failure_rows=[],
        execution_boundary={"platform_write": False},
    )
    db_session.add_all([_plan(), snapshot])
    db_session.commit()
    return snapshot, rows


def _draft_live_rows(rows, status):
    return [{
        "item_id": row["item_id"], "sku_id": row["sku_id"],
        "activity_price": row["signup_price"], "status": status,
    } for row in rows]


def test_draft_publish_is_retired_for_enrolled_paused_rows(db_session, monkeypatch):
    snapshot, rows = _draft_publish_snapshot(db_session, monkeypatch)
    refreshes = [
        {"ok": True, "rows": _draft_live_rows(rows, "暂停"),
         "export_evidence": {"filename": "pre.xlsx", "sha256": "1" * 64,
                             "size": 100}},
        {"ok": True, "rows": _draft_live_rows(rows, "已发布设定"),
         "export_evidence": {"filename": "post.xlsx", "sha256": "2" * 64,
                             "size": 101}},
    ]
    calls = []
    monkeypatch.setattr(
        campaign_service, "refresh_floor_evidence_from_current_activity",
        lambda *_args, **_kwargs: refreshes.pop(0))
    monkeypatch.setattr(
        web_agent_service, "publish_plan7_existing_super_reduce_drafts",
        lambda *_args, **_kwargs: calls.append(_kwargs) or {
            "ok": True, "published": True,
            "platform_write_observed": True, "job_id": "job-publish",
        })
    request = {
        "workflow_key": service.WORKFLOW_KEY,
        "expected_plan_id": 7,
        "expected_attempt_id": service.PARTIAL_ATTEMPT_ID,
        "expected_snapshot_id": snapshot.id,
        "expected_scope_sha256": service.DRAFT_PUBLISH_SCOPE_SHA256,
    }

    first = service.publish_plan7_existing_drafts(db_session, **request)
    replay = service.publish_plan7_existing_drafts(db_session, **request)

    assert first["ok"] is False
    assert first["error"] == "draft_publish_removed_paused_is_enrolled_state"
    assert first["execution_boundary"]["platform_write"] is False
    assert replay == first
    assert calls == []


def test_draft_publish_blocks_if_any_other_paused_item_exists(
        db_session, monkeypatch):
    snapshot, rows = _draft_publish_snapshot(db_session, monkeypatch)
    live = _draft_live_rows(rows, "暂停") + [{
        "item_id": "999999999999", "sku_id": "other",
        "activity_price": 1.0, "status": "暂停",
    }]
    called = []
    monkeypatch.setattr(
        campaign_service, "refresh_floor_evidence_from_current_activity",
        lambda *_args, **_kwargs: {"ok": True, "rows": live})
    monkeypatch.setattr(
        web_agent_service, "publish_plan7_existing_super_reduce_drafts",
        lambda *_args, **_kwargs: called.append(True))

    result = service.publish_plan7_existing_drafts(
        db_session, workflow_key=service.WORKFLOW_KEY,
        expected_plan_id=7, expected_attempt_id=service.PARTIAL_ATTEMPT_ID,
        expected_snapshot_id=snapshot.id,
        expected_scope_sha256=service.DRAFT_PUBLISH_SCOPE_SHA256)

    assert result["ok"] is False
    assert result["error"] == "draft_publish_removed_paused_is_enrolled_state"
    assert called == []
    assert service._load_draft_publish_receipt(db_session) is None


def test_draft_publish_unknown_terminal_is_claimed_and_never_retried(
        db_session, monkeypatch):
    snapshot, rows = _draft_publish_snapshot(db_session, monkeypatch)
    monkeypatch.setattr(
        campaign_service, "refresh_floor_evidence_from_current_activity",
        lambda *_args, **_kwargs: {
            "ok": True, "rows": _draft_live_rows(rows, "暂停")})
    calls = []
    monkeypatch.setattr(
        web_agent_service, "publish_plan7_existing_super_reduce_drafts",
        lambda *_args, **_kwargs: calls.append(True) or {
            "ok": False, "error": "confirmation_unknown",
            "platform_write_observed": None,
        })
    request = {
        "workflow_key": service.WORKFLOW_KEY,
        "expected_plan_id": 7,
        "expected_attempt_id": service.PARTIAL_ATTEMPT_ID,
        "expected_snapshot_id": snapshot.id,
        "expected_scope_sha256": service.DRAFT_PUBLISH_SCOPE_SHA256,
    }

    first = service.publish_plan7_existing_drafts(db_session, **request)
    second = service.publish_plan7_existing_drafts(db_session, **request)

    assert first["ok"] is False
    assert first["error"] == "draft_publish_removed_paused_is_enrolled_state"
    assert second == first
    assert calls == []
