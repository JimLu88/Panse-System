from copy import deepcopy
import inspect
import json
from pathlib import Path

from app.dependencies import (
    CAMPAIGN_PREPARE_SERVICE_PATHS,
    CAMPAIGN_WAREHOUSE_PRODUCT_PRICE_CORRECTION_PATH,
)
from app.services import campaign_warehouse_product_price_correction_service as service
from app.services import web_agent_service


def _readback(*, target: bool) -> dict:
    value = service.manifest(target=target)
    digest = service.TARGET_SHA256 if target else service.BASELINE_SHA256
    price = service.TARGET_PRICE if target else service.OLD_PRICE
    return {
        "ok": True, "manifest_sha256": digest,
        "manifest": {**value, "sha256": digest},
        "warehouse_state": {
            "item_id": service.ITEM_ID, "title": service.TITLE,
            "product_state": service.EXPECTED_PRODUCT_STATE,
            "min_price": price, "total_stock": service.TOTAL_STOCK,
        },
        "platform_product_write": False,
    }


def test_embedded_manifests_and_target_are_exact():
    baseline = service.manifest()
    target = service.manifest(target=True)

    assert len(baseline["rows"]) == 14
    assert baseline["rows"][-1] == [
        "尺寸微定制", "", service.SKU_ID, "0", "1500.00", "6", "", "上架"]
    assert target["rows"][-1][4] == "1420.00"
    assert target["rows"][:-1] == baseline["rows"][:-1]
    assert target["rows"][-1][:4] == baseline["rows"][-1][:4]
    assert target["rows"][-1][5:] == baseline["rows"][-1][5:]
    assert service.manifest_sha256(baseline) == service.BASELINE_SHA256
    assert service.manifest_sha256(target) == service.TARGET_SHA256


def test_historical_request_is_no_longer_executable_but_path_is_registered():
    payload = service.request_payload()

    assert not service.validate_request(payload)
    assert not service.validate_request({**payload, "target_price": "1419.99"})
    assert (CAMPAIGN_WAREHOUSE_PRODUCT_PRICE_CORRECTION_PATH
            in CAMPAIGN_PREPARE_SERVICE_PATHS)


def test_execute_is_user_rule_excluded_before_db_claim_or_web_agent(monkeypatch):
    class ForbiddenDb:
        def __getattr__(self, name):
            raise AssertionError(f"database access forbidden: {name}")

    result = service.execute(ForbiddenDb(), payload=service.request_payload())

    assert result["ok"] is False
    assert result["error"] == "user_rule_excluded"
    assert result["claim_created"] is False
    assert result["web_agent_called"] is False
    assert result["platform_write"] is False
    assert result["price_change"] is False
    assert result["execution_boundary"]["platform_product_write"] is False
    assert result["execution_boundary"]["price_change"] is False
    source = inspect.getsource(service.execute)
    assert "CampaignExecutionAttempt" not in source
    assert "web_agent_service" not in source
    assert ".commit(" not in source


def test_erp_web_agent_client_is_also_fail_closed(monkeypatch):
    monkeypatch.setattr(
        web_agent_service, "_post",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Web-Agent HTTP call forbidden")),
    )

    result = web_agent_service.correct_warehouse_product_price(
        object(), payload=service.request_payload())

    assert result["error"] == "user_rule_excluded"
    assert result["platform_write"] is False
    assert result["price_change"] is False
    assert result["web_agent_called"] is False
    source = inspect.getsource(web_agent_service.correct_warehouse_product_price)
    assert "_post(" not in source
    assert "wait_job(" not in source


def test_retired_cli_and_powershell_wrapper_do_not_contact_any_runtime(
        monkeypatch, capsys):
    from app.cli import campaign_correct_warehouse_product_sku_price as cli

    assert cli.main() == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["error"] == "user_rule_excluded"
    assert receipt["claim_created"] is False
    assert receipt["platform_write"] is False
    assert receipt["price_change"] is False
    source = inspect.getsource(cli)
    assert "urllib" not in source
    assert "SessionLocal" not in source
    assert "call_api" not in source

    wrapper = (
        Path(__file__).resolve().parents[2]
        / "scripts" / "campaign_correct_warehouse_product_sku_price_nas.ps1"
    ).read_text(encoding="utf-8")
    assert "user_rule_excluded" in wrapper
    assert "docker exec" not in wrapper
    assert "ssh.exe" not in wrapper
    assert "platform_write = $false" in wrapper
    assert "price_change = $false" in wrapper


def test_readback_requires_complete_manifest_and_warehouse_state():
    result = _readback(target=False)

    assert service._validate_readback(result, target=False) is None
    drift = deepcopy(result)
    drift["warehouse_state"]["product_state"] = "出售中"
    assert service._validate_readback(drift, target=False)["error"] == (
        "warehouse_product_price_readback_drift")
    drift = deepcopy(result)
    drift["manifest"]["rows"][0][4] = "20151.99"
    assert service._validate_readback(drift, target=False)["error"] == (
        "warehouse_product_price_readback_drift")


def test_terminal_requires_price_and_warehouse_conservation():
    result = {
        "ok": True, "submitted": True,
        "item_id": service.ITEM_ID, "sku_id": service.SKU_ID,
        "official_terminal": {
            "state": "complete", "price": service.TARGET_PRICE,
            "product_state": service.EXPECTED_PRODUCT_STATE,
            "source": "fresh_editor_and_in_stock_readback",
        },
        "before": {**service.manifest(), "sha256": service.BASELINE_SHA256},
        "staged": {**service.manifest(target=True),
                   "sha256": service.TARGET_SHA256},
        "readback": {**service.manifest(target=True),
                     "sha256": service.TARGET_SHA256},
        "before_state": {"product_state": service.EXPECTED_PRODUCT_STATE,
                         "min_price": service.OLD_PRICE,
                         "total_stock": service.TOTAL_STOCK},
        "after_state": {"product_state": service.EXPECTED_PRODUCT_STATE,
                        "min_price": service.TARGET_PRICE,
                        "total_stock": service.TOTAL_STOCK},
        "execution_boundary": {
            "platform_product_write": True, "account_action": True,
            "price_change": True,
            "stock_change": False, "title_change": False,
            "merchant_code_change": False, "sku_state_change": False,
            "product_state_change": False, "publish_now": False,
        },
    }

    assert service._terminal_exact(result)
    unsafe = deepcopy(result)
    unsafe["execution_boundary"]["publish_now"] = True
    assert not service._terminal_exact(unsafe)
    drift = deepcopy(result)
    drift["after_state"]["product_state"] = "出售中"
    assert not service._terminal_exact(drift)
