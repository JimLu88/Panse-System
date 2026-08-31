from app.cli import campaign_stage_lift_desk_sku_slot as cli
import inspect


def test_lift_desk_stage_manifest_is_fixed_and_unsaved(monkeypatch):
    assert cli.MANIFEST == {
        "item_id": "793202812082",
        "source_merchant_code": "PPS2441004051311",
        "target_merchant_code": "PPS2441004051311B1",
        "source_option": "130cm 带高台",
        "new_option": "130cm 带高台升降桌",
        "erp_price_guard": {
            "list_price": "9100.00", "daily_price": "6825.00",
            "small_promo": "4190.00", "mid_promo": "4050.00", "big_promo": "3830.00",
        },
    }


def test_web_agent_stage_helper_returns_terminal_job_result(monkeypatch, db_session):
    from app.services import web_agent_service

    monkeypatch.setattr(web_agent_service, "_post", lambda *a, **k: {
        "ok": True, "job": "job-stage"})
    monkeypatch.setattr(web_agent_service, "wait_job", lambda *a, **k: {
        "status": "done", "result": {
            "ok": True, "stopped_before": "提交宝贝信息",
            "platform_file_upload": True, "platform_product_write": False,
        }})
    result = web_agent_service.product_sku_slot_stage(db_session, cli.MANIFEST)
    assert result["ok"] is True
    assert result["job_id"] == "job-stage"
    assert result["platform_product_write"] is False


def test_cli_records_failed_preview_instead_of_claiming_created():
    source = inspect.getsource(cli.main)
    assert "mark_lift_desk_stage_failed" in source
    assert "mark_lift_desk_staged_unsaved" in source
    assert "if result.get(\"ok\")" in source
