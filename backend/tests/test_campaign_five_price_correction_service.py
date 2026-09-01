import inspect

import pytest

from app import dependencies
from app.services import campaign_five_price_correction_service as service


@pytest.mark.parametrize("phase", ["single_discount", "super_reduce"])
def test_request_is_fixed(phase):
    payload = service.request_payload(phase)
    assert service.validate_request(payload)
    assert payload["manifest_sha256"] == service.MANIFEST_SHA256
    assert payload["source_export_sha256"] == service.SOURCE_EXPORT_SHA256


def test_request_rejects_scope_drift():
    payload = service.request_payload("super_reduce")
    payload["manifest_sha256"] = "0" * 64
    assert service.validate_request(payload) is False
    with pytest.raises(ValueError, match="phase_invalid"):
        service.request_payload("all")


def test_durable_operation_names_fit_production_column():
    assert set(service.OPERATION_BY_PHASE) == service.PHASES
    assert all(len(name) <= 32 for name in service.OPERATION_BY_PHASE.values())
    assert len(set(service.OPERATION_BY_PHASE.values())) == len(service.PHASES)


def test_terminal_contract_has_exact_counts_and_no_write_boundaries():
    common = {
        "ok": True,
        "execution_boundary": {
            "automatic_retry": False,
            "withdraw_pause_remove": False,
            "daily_product_price_change": False,
            "warehouse_price_change": False,
            "zero_sales_item_touched": False,
            "platform_write": True,
        },
    }
    assert service._terminal_exact({
        **common, "submitted": True, "item_id": "717418169535",
        "row_count": 17}, "single_discount")
    assert service._terminal_exact({
        **common, "item_count": 4, "target_sku_count": 8,
        "zero_sales_excluded_item_id": "793202812082"}, "super_reduce")


def test_service_has_durable_claim_before_web_agent_call():
    source = inspect.getsource(service.execute)
    assert source.index("db.commit()") < source.index(
        "web_agent_service.correct_five_price")
    assert "automatic_retry_allowed=False" in source


def test_service_identity_is_allowed_only_on_five_price_route(monkeypatch):
    monkeypatch.setattr(
        dependencies.settings_service,
        "get",
        lambda _db, key, env_fallback=False: (
            "fixed-token"
            if key == dependencies.CAMPAIGN_PREPARE_SERVICE_SETTING
            else None
        ),
    )
    assert dependencies.CAMPAIGN_FIVE_PRICE_CORRECTION_PATH in (
        dependencies.CAMPAIGN_PREPARE_SERVICE_PATHS
    )
    assert dependencies.machine_identity_for_key(
        "fixed-token",
        object(),
        path=dependencies.CAMPAIGN_FIVE_PRICE_CORRECTION_PATH,
    ) == "service:campaign-prepare"
    assert dependencies.machine_identity_for_key(
        "fixed-token", object(), path="/api/campaigns"
    ) is None
