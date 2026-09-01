import inspect

from app.cli import campaign_recover_lift_desk_sku_slot_draft as cli
from app.services import sku_identity_service, web_agent_service


def _verified_recovery():
    return {
        "ok": True, "draft_saved": True, "listed": False,
        "campaign_status": "not_submitted", "platform_product_write": True,
        "automatic_retry_allowed": False,
        "draft_recovery": {"draft_id": "1355242198",
                           "saved_at": "2026-09-01 22:54:21",
                           "initial_option_count": 12},
        "readback": {"ok": True, "item_id": "793202812082",
                     "target_merchant_code": "PPS2441004051311B1",
                     "option_count": 13, "sku_row_count": 13, "diff": [],
                     "rendered_missing": [], "input_value_missing": [],
                     "invalid_rows": []},
    }


def test_recovery_cli_uses_dedicated_existing_draft_entry():
    source = inspect.getsource(cli.main)
    assert "product_sku_slot_draft_recover" in source
    assert "mark_lift_desk_draft_save_result" in source
    assert "product_sku_slot_draft_save" not in source


def test_recovery_helper_calls_only_recovery_route(monkeypatch, db_session):
    called = {}

    def fake_post(db, path, payload, timeout):
        called["path"] = path
        return {"ok": True, "job": "job-recovery"}

    monkeypatch.setattr(web_agent_service, "_post", fake_post)
    monkeypatch.setattr(web_agent_service, "wait_job", lambda *a, **k: {
        "status": "done", "result": _verified_recovery()})
    result = web_agent_service.product_sku_slot_draft_recover(
        db_session, cli.MANIFEST)
    assert called["path"] == "/api/product-sku/slot-draft-recover"
    assert result["ok"] is True
    assert result["job_id"] == "job-recovery"


def test_verified_recovery_closes_ledger_and_keeps_campaign_unsubmitted(db_session):
    sku_identity_service.ensure_lift_desk_proposal(
        db_session, authorization_ref=cli.AUTHORIZATION_REF)
    receipt = sku_identity_service.mark_lift_desk_draft_save_result(
        db_session, result=_verified_recovery(),
        required_recovery_identity=cli.RECOVERY_IDENTITY)
    assert receipt["state"] == "saved_draft_verified"
    assert receipt["product_save_status"] == "saved_draft_verified"
    assert receipt["campaign_signup_status"] == "not_submitted"
    assert receipt["automatic_retry_allowed"] is False


def test_wrong_existing_draft_identity_is_never_marked_verified(db_session):
    sku_identity_service.ensure_lift_desk_proposal(
        db_session, authorization_ref=cli.AUTHORIZATION_REF)
    result = _verified_recovery()
    result["draft_recovery"]["draft_id"] = "999"
    receipt = sku_identity_service.mark_lift_desk_draft_save_result(
        db_session, result=result,
        required_recovery_identity=cli.RECOVERY_IDENTITY)
    assert receipt["state"] == "draft_save_result_unknown"
    assert receipt["product_save_status"] == "result_unknown"
    assert receipt["automatic_retry_allowed"] is False
