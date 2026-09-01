import inspect

from app.cli import campaign_readback_lift_desk_sku_slot_draft as cli
from app.services import sku_identity_service, web_agent_service


def _verified_readback():
    return {
        "ok": True,
        "draft_saved": True,
        "listed": False,
        "campaign_status": "not_submitted",
        "submission_action": False,
        "read_only": True,
        "platform_product_write": False,
        "automatic_retry_allowed": False,
        "readback": {
            "ok": True,
            "item_id": "793202812082",
            "target_merchant_code": "PPS2441004051311B1",
            "option_count": 13,
            "sku_row_count": 13,
            "diff": [],
            "rendered_missing": [],
            "input_value_missing": [],
            "invalid_rows": [],
            "preexisting_sku_diff": [],
        },
    }


def test_readback_cli_reuses_manifest_and_never_calls_save_entry():
    assert cli.MANIFEST["item_id"] == "793202812082"
    source = inspect.getsource(cli.main)
    assert "product_sku_slot_draft_readback" in source
    assert "mark_lift_desk_draft_readback_result" in source
    assert "product_sku_slot_draft_save" not in source


def test_web_agent_readback_helper_calls_read_only_route(monkeypatch, db_session):
    monkeypatch.setattr(web_agent_service, "_post", lambda *a, **k: {
        "ok": True, "job": "job-draft-readback"})
    monkeypatch.setattr(web_agent_service, "wait_job", lambda *a, **k: {
        "status": "done", "result": _verified_readback()})
    result = web_agent_service.product_sku_slot_draft_readback(
        db_session, cli.MANIFEST)
    assert result["ok"] is True
    assert result["job_id"] == "job-draft-readback"
    assert result["platform_product_write"] is False


def test_verified_readback_closes_unknown_state_without_campaign_signup(db_session):
    sku_identity_service.ensure_lift_desk_proposal(
        db_session, authorization_ref="user:2026-09-01:save-lift-desk-sku-draft")
    sku_identity_service.mark_lift_desk_draft_save_result(db_session, result={
        "ok": False,
        "error": "draft_save_readback_unverified",
        "platform_product_write": True,
        "automatic_retry_allowed": False,
        "campaign_status": "not_submitted",
    })
    receipt = sku_identity_service.mark_lift_desk_draft_readback_result(
        db_session, result=_verified_readback())
    assert receipt["verified"] is True
    assert receipt["state"] == "saved_draft_verified"
    assert receipt["product_save_status"] == "saved_draft_verified"
    assert receipt["campaign_signup_status"] == "not_submitted"
    assert receipt["automatic_retry_allowed"] is False


def test_failed_readback_does_not_demote_or_falsely_verify_unknown_state(db_session):
    sku_identity_service.ensure_lift_desk_proposal(
        db_session, authorization_ref="user:2026-09-01:save-lift-desk-sku-draft")
    before = sku_identity_service.mark_lift_desk_draft_save_result(db_session, result={
        "ok": False,
        "error": "draft_save_readback_unverified",
        "platform_product_write": True,
        "automatic_retry_allowed": False,
        "campaign_status": "not_submitted",
    })
    receipt = sku_identity_service.mark_lift_desk_draft_readback_result(
        db_session, result={"ok": False, "read_only": True,
                            "platform_product_write": False,
                            "error": "temporary_readback_failure"})
    assert before["state"] == "draft_save_result_unknown"
    assert receipt["verified"] is False
    assert receipt["state"] == "draft_save_result_unknown"
    assert receipt["product_save_status"] == "result_unknown"
    assert receipt["campaign_signup_status"] == "not_submitted"
