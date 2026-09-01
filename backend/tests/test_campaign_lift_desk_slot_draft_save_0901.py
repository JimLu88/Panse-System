import inspect

from app.cli import campaign_save_lift_desk_sku_slot_draft as cli
from app.services import sku_identity_service, web_agent_service


def _verified_result(*, platform_write=True):
    return {
        "ok": True,
        "draft_saved": True,
        "listed": False,
        "campaign_status": "not_submitted",
        "platform_product_write": platform_write,
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


def test_draft_save_cli_reuses_exact_reviewed_manifest():
    assert cli.MANIFEST["item_id"] == "793202812082"
    assert cli.MANIFEST["target_merchant_code"] == "PPS2441004051311B1"
    assert cli.MANIFEST["new_option"] == "130cm 带高台升降桌"
    source = inspect.getsource(cli.main)
    assert "product_sku_slot_draft_save" in source
    assert "mark_lift_desk_draft_save_result" in source


def test_web_agent_draft_save_helper_returns_terminal_result(monkeypatch, db_session):
    monkeypatch.setattr(web_agent_service, "_post", lambda *a, **k: {
        "ok": True, "job": "job-draft-save"})
    monkeypatch.setattr(web_agent_service, "wait_job", lambda *a, **k: {
        "status": "done", "result": _verified_result()})
    result = web_agent_service.product_sku_slot_draft_save(db_session, cli.MANIFEST)
    assert result["ok"] is True
    assert result["job_id"] == "job-draft-save"
    assert result["listed"] is False


def test_verified_draft_save_updates_ledger_without_campaign_signup(db_session):
    sku_identity_service.ensure_lift_desk_proposal(
        db_session, authorization_ref=cli.AUTHORIZATION_REF)
    receipt = sku_identity_service.mark_lift_desk_draft_save_result(
        db_session, result=_verified_result())
    assert receipt["state"] == "saved_draft_verified"
    assert receipt["product_save_status"] == "saved_draft_verified"
    assert receipt["campaign_signup_status"] == "not_submitted"
    assert receipt["automatic_retry_allowed"] is False


def test_post_click_unknown_is_no_retry_and_not_marked_saved(db_session):
    sku_identity_service.ensure_lift_desk_proposal(
        db_session, authorization_ref=cli.AUTHORIZATION_REF)
    receipt = sku_identity_service.mark_lift_desk_draft_save_result(
        db_session, result={
            "ok": False,
            "error": "draft_save_readback_unverified",
            "platform_product_write": True,
            "automatic_retry_allowed": False,
            "campaign_status": "not_submitted",
        })
    assert receipt["state"] == "draft_save_result_unknown"
    assert receipt["product_save_status"] == "result_unknown"
    assert receipt["campaign_signup_status"] == "not_submitted"
    assert receipt["automatic_retry_allowed"] is False
