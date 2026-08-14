import json
import time
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import tachikoma_connection as connection


def test_connection_port_default_and_environment_override():
    assert connection.configured_port({}) == 8000
    assert connection.configured_port({"PORT": "8123"}) == 8123
    assert connection.configured_erp_read_base_url({
        connection.ERP_READ_BASE_URL_ENV: "http://192.168.31.21:8200/",
    }) == "http://192.168.31.21:8200"


def test_connection_mode_and_route_whitelist_are_fail_closed():
    assert connection.connection_only({}) is False
    assert connection.connection_only({connection.CONNECTION_ONLY_ENV: "1"}) is True
    assert connection.connection_path_allowed("GET", "/api/health") is True
    assert connection.connection_path_allowed("GET", "/api/version") is True
    assert connection.connection_path_allowed("GET", "/api/tachikoma/product-candidates") is True
    assert connection.connection_path_allowed("GET", "/api/products") is False
    assert connection.connection_path_allowed("GET", "/api/orders") is False
    assert connection.connection_path_allowed("POST", "/api/version") is False


def test_health_is_stable_credential_free_identity():
    response = TestClient(app).get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "ok": True,
        "service": "panse-system",
        "version": "1.0.0",
        "contract_version": "v1",
        "ready": True,
        "dependencies_ready": True,
        "mode": "connection_only",
    }


def test_identity_mismatch_fails_closed():
    with pytest.raises(connection.ConnectionContractError) as caught:
        connection.validate_identity({
            "service": "generic-ok",
            "contract_version": "v1",
            "ready": True,
        })
    assert caught.value.code == "identity_mismatch"


def test_public_read_only_action_needs_no_credentials_and_is_idempotent():
    calls = []

    def transport(**kwargs):
        calls.append(kwargs)
        return {"commit": "test123"}

    first = connection.invoke(
        "product-content-chain", "product-facts",
        method="GET", path="/api/tachikoma/product-candidates", transport=transport,
    )
    second = connection.invoke(
        "product-content-chain", "product-facts",
        method="GET", path="/api/tachikoma/product-candidates", transport=transport,
    )
    assert first == second
    assert first["idempotency_key"] == second["idempotency_key"]
    assert len(calls) == 2
    assert all(call["credentials"] is None for call in calls)


def test_protected_erp_read_requires_credentials():
    with pytest.raises(connection.ConnectionContractError) as caught:
        connection.invoke(
            "commerce-intelligence-chain", "erp",
            method="GET", path="/api/web-agent/status",
            transport=lambda **kwargs: {},
        )
    assert caught.value.code == "credentials_required"


def test_missing_rules_contract_and_write_without_approval_are_blocked():
    with pytest.raises(connection.ConnectionContractError) as caught:
        connection.invoke(
            "customer-conversation-chain", "rules",
            method="GET", path="/api/version",
            transport=lambda **kwargs: {},
        )
    assert caught.value.code == "approval_required"

    with pytest.raises(connection.ConnectionContractError) as caught:
        connection.invoke(
            "customer-conversation-chain", "rules",
            method="GET", path="/api/version",
            approval="project_adapter_required",
            transport=lambda **kwargs: {},
        )
    assert caught.value.code == "action_disabled"

    with pytest.raises(connection.ConnectionContractError) as caught:
        connection.invoke(
            "product-content-chain", "product-facts",
            method="POST", path="/api/tachikoma/product-candidates",
            transport=lambda **kwargs: {},
        )
    assert caught.value.code == "approval_required"


def test_timeout_and_dependency_failure_fail_closed():
    def slow(**kwargs):
        time.sleep(0.05)
        return {}

    with pytest.raises(connection.ConnectionContractError) as caught:
        connection.invoke(
            "product-content-chain", "product-facts",
            method="GET", path="/api/tachikoma/product-candidates", timeout_seconds=0.001,
            transport=slow,
        )
    assert caught.value.code == "timeout"

    def broken(**kwargs):
        raise RuntimeError("secret detail must not leak")

    with pytest.raises(connection.ConnectionContractError) as caught:
        connection.invoke(
            "product-content-chain", "product-facts",
            method="GET", path="/api/tachikoma/product-candidates", transport=broken,
        )
    assert caught.value.code == "dependency_failure"
    assert "secret detail" not in str(caught.value)


def test_no_default_transport_can_execute_production_call():
    with pytest.raises(connection.ConnectionContractError) as caught:
        connection.invoke(
            "product-content-chain", "product-facts",
            method="GET", path="/api/tachikoma/product-candidates",
        )
    assert caught.value.code == "production_execution_disabled"


def test_connection_manifest_is_fail_closed_and_complete():
    path = Path(__file__).resolve().parents[2] / "tachikoma-connection.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "tachikoma-connection/v1"
    assert manifest["software_id"] == manifest["service"] == "panse-system"
    assert manifest["default_port"] == 8000
    assert manifest["health_path"] == "/api/health"
    assert manifest["production_execution_enabled"] is False
    assert manifest["upgrade_execution_enabled"] is False
    assert {(item["chain_id"], item["step"]) for item in manifest["business_actions"]} == set(connection.ACTIONS)
    assert manifest["acceptance_evidence"] == {
        "sandbox_business_flow_verified": False,
        "live_business_result_verified": False,
        "rollback_drill_verified": False,
        "evidence_refs": [],
    }


def test_live_connection_mode_blocks_business_reads_and_writes(monkeypatch):
    monkeypatch.setenv(connection.CONNECTION_ONLY_ENV, "1")
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/version").status_code == 200

        read = client.get("/api/orders")
        write = client.post("/api/auth/login", json={})

    assert read.status_code == 403
    assert write.status_code == 403
    assert read.json()["code"] == "PRODUCTION_EXECUTION_DISABLED"
    assert write.json()["code"] == "PRODUCTION_EXECUTION_DISABLED"


def test_product_candidates_returns_only_bounded_non_sensitive_fields(monkeypatch):
    monkeypatch.setenv(connection.ERP_READ_BASE_URL_ENV, "http://erp.test")

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def read(self, _limit):
            return json.dumps({"items": [{
                "product_code": "P001",
                "product_name": "测试产品",
                "sku_code": "SKU001",
                "sku": "标准款",
                "daily_price": "199.90",
                "is_custom_placeholder": False,
                "accounting_cost": "88.00",
                "campaign": "must-not-pass-through",
            }]}).encode("utf-8")

    monkeypatch.setattr(connection.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    result = connection.product_candidates(product_code=None, limit=20)

    assert result.read_only is True
    assert result.mutations == 0
    assert result.returned == 1
    assert result.candidates[0].model_dump() == {
        "product_code": "P001",
        "product_name": "测试产品",
        "sku_code": "SKU001",
        "sku": "标准款",
        "daily_price": "199.90",
    }
    assert set(result.candidates[0].model_dump()) == {
        "product_code", "product_name", "sku_code", "sku", "daily_price",
    }
