import hashlib
import inspect
import json
from datetime import datetime

import pytest

from app import dependencies
from app.cli import (
    campaign_recover_five_price_single_discount as recovery_cli,
    campaign_recover_five_price_super_reduce as super_recovery_cli,
    campaign_recover_five_price_super_reduce_v2 as super_recovery_v2_cli,
    campaign_recover_five_price_super_reduce_v3 as super_recovery_v3_cli,
)
from app.models.campaign import CampaignExecutionAttempt, CampaignPlan
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


def test_recovery_request_is_bound_to_exact_write_free_timeout_attempt():
    payload = service.recovery_request_payload()
    assert service.validate_recovery_request(payload)
    assert payload["phase"] == "single_discount"
    assert payload["expected_failed_attempt_id"] == (
        "b8b0ddcb5633cbe6a1b69681")
    assert payload["confirmed_no_platform_write"] is True
    payload["expected_failed_attempt_id"] = "0" * 24
    assert service.validate_recovery_request(payload) is False


def test_super_recovery_request_is_bound_to_exact_write_free_locator_attempt():
    payload = service.super_recovery_request_payload()
    assert service.validate_super_recovery_request(payload)
    assert payload["phase"] == "super_reduce"
    assert payload["expected_failed_attempt_id"] == (
        "5373510a5a33b115e4771f37")
    assert payload["confirmed_no_platform_write"] is True
    payload["expected_failed_attempt_id"] = "0" * 24
    assert service.validate_super_recovery_request(payload) is False


def test_super_recovery_v2_request_is_bound_to_exact_unsaved_attempt():
    payload = service.super_recovery_v2_request_payload()
    assert service.validate_super_recovery_v2_request(payload)
    assert payload["phase"] == "super_reduce"
    assert payload["expected_failed_attempt_id"] == (
        "b3f1282a5b34b0181d1daa95")
    assert payload["confirmed_no_persisted_change"] is True
    payload["confirmed_no_persisted_change"] = False
    assert service.validate_super_recovery_v2_request(payload) is False


def test_super_recovery_v3_request_is_bound_to_exact_partial_attempt():
    payload = service.super_recovery_v3_request_payload()
    assert service.validate_super_recovery_v3_request(payload)
    assert payload["expected_failed_attempt_id"] == (
        "797c3857548bd818cf6065fb")
    assert payload["confirmed_partial_persisted_change"] is True
    assert payload["persisted_item_id"] == "1046992283533"
    payload["persisted_item_id"] = "717418169535"
    assert service.validate_super_recovery_v3_request(payload) is False


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


def _plan(db_session):
    row = CampaignPlan(
        id=service.PLAN_ID, workflow_key=service.WORKFLOW_KEY,
        name="plan7", campaign_type="super_reduce", tier="mid",
        start_at=datetime(2026, 9, 1), end_at=datetime(2026, 9, 5, 23, 59, 59),
        qn_campaign_title="超级立减", status="alarmed",
        platform_activity_mode="long_running_update",
    )
    db_session.add(row)
    db_session.commit()
    return row


def _failed_attempt(db_session, **updates):
    terminal_error = (
        "ConnectTimeout: HTTPConnectionPool(host='192.168.31.91', port=8500): "
        "Max retries exceeded with url: /api/campaign/five-price-correction "
        "(Caused by ConnectTimeoutError: Connection to 192.168.31.91 timed out.)"
    )
    values = {
        "id": service.RECOVERY_FAILED_ATTEMPT_ID,
        "plan_id": service.PLAN_ID,
        "workflow_key": service.WORKFLOW_KEY,
        "operation": service.OPERATION_BY_PHASE["single_discount"],
        "scope_sha256": service._scope_sha("single_discount"),
        "state": "unknown", "write_claimed": True,
        "platform_write_observed": None,
        "automatic_retry_allowed": False,
        "request_id": "five-price-original",
        "web_agent_job_id": None,
        "last_step": "terminal_not_exact",
        "error_code": "ConnectTimeout: HTTPConnectionPool",
        "result_summary": {
            "request": service.request_payload("single_discount"),
            "terminal_ok": False, "submitted": None,
            "terminal_error": terminal_error,
        },
    }
    values.update(updates)
    row = CampaignExecutionAttempt(**values)
    db_session.add(row)
    db_session.commit()
    return row


def _single_terminal():
    return {
        "ok": True, "submitted": True, "item_id": "717418169535",
        "row_count": 17, "web_agent_job_id": "job-recovery",
        "execution_boundary": {
            "automatic_retry": False,
            "withdraw_pause_remove": False,
            "daily_product_price_change": False,
            "warehouse_price_change": False,
            "zero_sales_item_touched": False,
            "platform_write": True,
        },
    }


def _completed_single_attempt(db_session):
    row = CampaignExecutionAttempt(
        id="1" * 24,
        plan_id=service.PLAN_ID,
        workflow_key=service.WORKFLOW_KEY,
        operation=service.RECOVERY_OPERATION,
        scope_sha256="1" * 64,
        state="completed",
        write_claimed=True,
        platform_write_observed=True,
        automatic_retry_allowed=False,
        request_id="completed-single",
    )
    db_session.add(row)
    db_session.commit()
    return row


def _failed_super_attempt(db_session, **updates):
    terminal_error = (
        "RuntimeError: 商品 1046992283533 指定 SKU 当前价变化: "
        "{'6241476755540': {'expected': '388.00', "
        "'actual': '6241476755540.00'}}"
    )
    values = {
        "id": service.SUPER_RECOVERY_FAILED_ATTEMPT_ID,
        "plan_id": service.PLAN_ID,
        "workflow_key": service.WORKFLOW_KEY,
        "operation": service.OPERATION_BY_PHASE["super_reduce"],
        "scope_sha256": service._scope_sha("super_reduce"),
        "state": "failed", "write_claimed": True,
        "platform_write_observed": False,
        "automatic_retry_allowed": False,
        "request_id": "five-price-super-original",
        "web_agent_job_id": "job1",
        "last_step": "terminal_not_exact",
        "error_code": terminal_error,
        "result_summary": {
            "request": service.request_payload("super_reduce"),
            "terminal_ok": False, "submitted": False,
            "partial_success_item_ids": [],
            "terminal_error": terminal_error,
        },
    }
    values.update(updates)
    row = CampaignExecutionAttempt(**values)
    db_session.add(row)
    db_session.commit()
    return row


def _super_terminal():
    return {
        "ok": True, "submitted": True,
        "item_count": 4, "target_sku_count": 8,
        "zero_sales_excluded_item_id": "793202812082",
        "web_agent_job_id": "job-super-recovery",
        "execution_boundary": {
            "automatic_retry": False,
            "withdraw_pause_remove": False,
            "daily_product_price_change": False,
            "warehouse_price_change": False,
            "zero_sales_item_touched": False,
            "platform_write": True,
        },
    }


def _failed_super_v2_attempt(db_session, **updates):
    terminal_error = "RuntimeError: 超级立减最终提交按钮不唯一或不存在"
    values = {
        "id": service.SUPER_RECOVERY_V2_FAILED_ATTEMPT_ID,
        "plan_id": service.PLAN_ID,
        "workflow_key": service.WORKFLOW_KEY,
        "operation": service.SUPER_RECOVERY_OPERATION,
        "scope_sha256": "a30e18391b256419e547f99d91079b6c98dc77ce6e62329f1fb37aed2ac9d99e",
        "state": "unknown", "write_claimed": True,
        "platform_write_observed": True,
        "automatic_retry_allowed": False,
        "request_id": "five-price-super-recovery-v1",
        "web_agent_job_id": "job1",
        "last_step": "super_recovery_terminal_not_exact",
        "error_code": terminal_error,
        "result_summary": {
            "request": service.super_recovery_request_payload(),
            "failed_attempt_id": service.SUPER_RECOVERY_FAILED_ATTEMPT_ID,
            "failed_attempt_preserved": True,
            "terminal_ok": False, "submitted": True,
            "partial_success_item_ids": [],
            "terminal_error": terminal_error,
        },
    }
    values.update(updates)
    row = CampaignExecutionAttempt(**values)
    db_session.add(row)
    db_session.commit()
    return row


def _failed_super_v3_attempt(db_session, **updates):
    terminal_error = (
        "TimeoutError: Locator.click: Timeout 5000ms exceeded.\n"
        "waiting for [data-pw-super-item-card='1046992283533']; "
        "next-overlay-wrapper opened intercepts pointer events"
    )
    preflight = {
        "ok": True,
        "items": [
            {"item_id": item_id, "platform_write": False}
            for item_id in service.SUPER_RECOVERY_V2_OLD_TARGETS
        ],
    }
    v2_payload = service.super_recovery_v2_request_payload()
    values = {
        "id": service.SUPER_RECOVERY_V3_FAILED_ATTEMPT_ID,
        "plan_id": service.PLAN_ID,
        "workflow_key": service.WORKFLOW_KEY,
        "operation": service.SUPER_RECOVERY_V2_OPERATION,
        "scope_sha256": hashlib.sha256(json.dumps(
            v2_payload, sort_keys=True,
            separators=(",", ":")).encode()).hexdigest(),
        "state": "unknown", "write_claimed": True,
        "platform_write_observed": True,
        "automatic_retry_allowed": False,
        "request_id": "five-price-super-recovery-v2-partial",
        "web_agent_job_id": "job6",
        "last_step": "super_recovery_v2_terminal_not_exact",
        "error_code": terminal_error[:128],
        "result_summary": {
            "request": v2_payload,
            "failed_attempt_id": service.SUPER_RECOVERY_V2_FAILED_ATTEMPT_ID,
            "failed_attempt_preserved": True,
            "preflight_readback": preflight,
            "terminal_ok": False, "submitted": True,
            "partial_success_item_ids": [],
            "terminal_error": terminal_error,
        },
    }
    values.update(updates)
    row = CampaignExecutionAttempt(**values)
    db_session.add(row)
    db_session.commit()
    return row


def _super_readback(item_id):
    targets = service.SUPER_RECOVERY_V2_OLD_TARGETS[item_id]
    return {
        "ok": True,
        "status": "活动中",
        "web_agent_job_id": f"readback-{item_id}",
        "editor_inventory": {
            "rows": [
                {"sku_id": sku_id, "inputs": [{"value": price}]}
                for sku_id, price in targets.items()
            ],
        },
        "execution_boundary": {"platform_write": False},
    }


def _super_readback_v3(item_id):
    targets = service.SUPER_RECOVERY_V3_EXPECTED_TARGETS[item_id]
    return {
        "ok": True,
        "status": "活动中",
        "web_agent_job_id": f"readback-v3-{item_id}",
        "editor_inventory": {
            "rows": [
                {"sku_id": sku_id, "inputs": [{"value": price}]}
                for sku_id, price in targets.items()
            ],
        },
        "execution_boundary": {"platform_write": False},
    }


def test_recovery_preserves_original_and_creates_separate_one_shot(
        db_session, monkeypatch):
    _plan(db_session)
    original = _failed_attempt(db_session)
    original_summary = dict(original.result_summary)
    monkeypatch.setattr(
        service.web_agent_service, "correct_five_price",
        lambda _db, *, payload, timeout_s: _single_terminal())

    result = service.recover_single_discount(
        db_session, payload=service.recovery_request_payload())

    assert result["ok"] is True
    assert result["recovered_failed_attempt_id"] == original.id
    db_session.refresh(original)
    assert original.state == "unknown"
    assert original.web_agent_job_id is None
    assert original.result_summary == original_summary
    recovery = db_session.get(
        CampaignExecutionAttempt, result["attempt_id"])
    assert recovery.operation == service.RECOVERY_OPERATION
    assert recovery.state == "completed"
    assert recovery.web_agent_job_id == "job-recovery"
    assert recovery.automatic_retry_allowed is False

    second = service.recover_single_discount(
        db_session, payload=service.recovery_request_payload())
    assert second["ok"] is False
    assert second["error"] == "five_price_recovery_already_consumed_no_retry"


def test_super_recovery_preserves_original_and_is_one_shot(
        db_session, monkeypatch):
    _plan(db_session)
    _completed_single_attempt(db_session)
    original = _failed_super_attempt(db_session)
    original_summary = dict(original.result_summary)
    monkeypatch.setattr(
        service.web_agent_service, "correct_five_price",
        lambda _db, *, payload, timeout_s: _super_terminal())

    result = service.recover_super_reduce(
        db_session, payload=service.super_recovery_request_payload())

    assert result["ok"] is True
    assert result["recovered_failed_attempt_id"] == original.id
    db_session.refresh(original)
    assert original.state == "failed"
    assert original.platform_write_observed is False
    assert original.result_summary == original_summary
    recovery = db_session.get(
        CampaignExecutionAttempt, result["attempt_id"])
    assert recovery.operation == service.SUPER_RECOVERY_OPERATION
    assert recovery.state == "completed"
    assert recovery.web_agent_job_id == "job-super-recovery"
    assert recovery.automatic_retry_allowed is False

    second = service.recover_super_reduce(
        db_session, payload=service.super_recovery_request_payload())
    assert second["ok"] is False
    assert second["error"] == (
        "five_price_super_recovery_already_consumed_no_retry")


def test_super_recovery_v2_readbacks_all_four_before_claim(
        db_session, monkeypatch):
    _plan(db_session)
    _completed_single_attempt(db_session)
    original = _failed_super_v2_attempt(db_session)
    original_summary = dict(original.result_summary)
    read_items = []

    def inspect(_db, *, item_id, timeout_s):
        read_items.append(item_id)
        return _super_readback(item_id)

    monkeypatch.setattr(
        service.web_agent_service, "inspect_super_reduce_item", inspect)
    monkeypatch.setattr(
        service.web_agent_service, "correct_five_price",
        lambda _db, *, payload, timeout_s: _super_terminal())

    result = service.recover_super_reduce_v2(
        db_session, payload=service.super_recovery_v2_request_payload())

    assert result["ok"] is True
    assert read_items == list(service.SUPER_RECOVERY_V2_OLD_TARGETS)
    assert len(result["preflight_readback"]["items"]) == 4
    db_session.refresh(original)
    assert original.state == "unknown"
    assert original.platform_write_observed is True
    assert original.result_summary == original_summary
    recovery = db_session.get(
        CampaignExecutionAttempt, result["attempt_id"])
    assert recovery.operation == service.SUPER_RECOVERY_V2_OPERATION
    assert recovery.state == "completed"
    assert len(recovery.result_summary["preflight_readback"]["items"]) == 4

    second = service.recover_super_reduce_v2(
        db_session, payload=service.super_recovery_v2_request_payload())
    assert second["error"] == (
        "five_price_super_recovery_v2_already_consumed_no_retry")


def test_super_recovery_v2_stops_before_claim_on_price_drift(
        db_session, monkeypatch):
    _plan(db_session)
    _completed_single_attempt(db_session)
    _failed_super_v2_attempt(db_session)

    def inspect(_db, *, item_id, timeout_s):
        result = _super_readback(item_id)
        if item_id == "1046992283533":
            result["editor_inventory"]["rows"][0]["inputs"][0]["value"] = "285"
        return result

    monkeypatch.setattr(
        service.web_agent_service, "inspect_super_reduce_item", inspect)
    monkeypatch.setattr(
        service.web_agent_service, "correct_five_price",
        lambda *_args, **_kwargs: pytest.fail("write must not start"))

    result = service.recover_super_reduce_v2(
        db_session, payload=service.super_recovery_v2_request_payload())

    assert result["error"] == (
        "five_price_super_recovery_v2_price_not_original")
    assert db_session.query(CampaignExecutionAttempt).filter_by(
        operation=service.SUPER_RECOVERY_V2_OPERATION).count() == 0


def test_super_recovery_v3_proves_one_new_three_old_before_claim(
        db_session, monkeypatch):
    _plan(db_session)
    _completed_single_attempt(db_session)
    original = _failed_super_v3_attempt(db_session)
    original_summary = dict(original.result_summary)
    read_items = []

    def inspect_item(_db, *, item_id, timeout_s):
        read_items.append(item_id)
        return _super_readback_v3(item_id)

    monkeypatch.setattr(
        service.web_agent_service, "inspect_super_reduce_item", inspect_item)
    monkeypatch.setattr(
        service.web_agent_service, "correct_five_price",
        lambda _db, *, payload, timeout_s: _super_terminal())

    result = service.recover_super_reduce_v3(
        db_session, payload=service.super_recovery_v3_request_payload())

    assert result["ok"] is True
    assert read_items == list(service.SUPER_RECOVERY_V3_EXPECTED_TARGETS)
    assert len(result["preflight_readback"]["items"]) == 4
    db_session.refresh(original)
    assert original.state == "unknown"
    assert original.result_summary == original_summary
    recovery = db_session.get(
        CampaignExecutionAttempt, result["attempt_id"])
    assert recovery.operation == service.SUPER_RECOVERY_V3_OPERATION
    assert recovery.state == "completed"
    assert recovery.result_summary["persisted_item_id"] == "1046992283533"
    assert recovery.result_summary["remaining_item_ids"] == [
        "717418169535", "840643621692", "840659847455"]

    second = service.recover_super_reduce_v3(
        db_session, payload=service.super_recovery_v3_request_payload())
    assert second["error"] == (
        "five_price_super_recovery_v3_already_consumed_no_retry")


def test_super_recovery_v3_stops_before_claim_on_mixed_state_drift(
        db_session, monkeypatch):
    _plan(db_session)
    _completed_single_attempt(db_session)
    _failed_super_v3_attempt(db_session)

    def inspect_item(_db, *, item_id, timeout_s):
        result = _super_readback_v3(item_id)
        if item_id == "717418169535":
            result["editor_inventory"]["rows"][0]["inputs"][0]["value"] = "288"
        return result

    monkeypatch.setattr(
        service.web_agent_service, "inspect_super_reduce_item", inspect_item)
    monkeypatch.setattr(
        service.web_agent_service, "correct_five_price",
        lambda *_args, **_kwargs: pytest.fail("write must not start"))

    result = service.recover_super_reduce_v3(
        db_session, payload=service.super_recovery_v3_request_payload())

    assert result["error"] == (
        "five_price_super_recovery_v3_price_state_drift")
    assert db_session.query(CampaignExecutionAttempt).filter_by(
        operation=service.SUPER_RECOVERY_V3_OPERATION).count() == 0


@pytest.mark.parametrize("updates", [
    {"state": "unknown"},
    {"platform_write_observed": None},
    {"web_agent_job_id": "job2"},
    {"last_step": "another_step"},
])
def test_super_recovery_rejects_any_original_attempt_drift(
        db_session, monkeypatch, updates):
    _plan(db_session)
    _completed_single_attempt(db_session)
    _failed_super_attempt(db_session, **updates)
    monkeypatch.setattr(
        service.web_agent_service, "correct_five_price",
        lambda *_args, **_kwargs: pytest.fail("Web-Agent must not be called"))
    result = service.recover_super_reduce(
        db_session, payload=service.super_recovery_request_payload())
    assert result["error"] == (
        "five_price_super_recovery_failed_attempt_not_write_free")


@pytest.mark.parametrize("updates", [
    {"state": "failed"},
    {"platform_write_observed": False},
    {"web_agent_job_id": "job-maybe-reached"},
    {"last_step": "another_step"},
])
def test_recovery_rejects_any_original_attempt_drift(
        db_session, monkeypatch, updates):
    _plan(db_session)
    _failed_attempt(db_session, **updates)
    monkeypatch.setattr(
        service.web_agent_service, "correct_five_price",
        lambda *_args, **_kwargs: pytest.fail("Web-Agent must not be called"))
    result = service.recover_single_discount(
        db_session, payload=service.recovery_request_payload())
    assert result["error"] == (
        "five_price_recovery_failed_attempt_not_write_free")


def test_super_reduce_requires_completed_single_discount(
        db_session, monkeypatch):
    _plan(db_session)
    monkeypatch.setattr(
        service.web_agent_service, "correct_five_price",
        lambda *_args, **_kwargs: pytest.fail("Web-Agent must not be called"))
    result = service.execute(
        db_session, payload=service.request_payload("super_reduce"))
    assert result["error"] == "five_price_single_discount_not_completed"


def test_recovery_cli_has_fixed_endpoint_and_payload(monkeypatch):
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def read():
            return b'{"ok":true}'

    class Opener:
        @staticmethod
        def open(req, timeout):
            captured["url"] = req.full_url
            captured["token"] = req.get_header("X-api-key")
            captured["body"] = req.data
            captured["timeout"] = timeout
            return Response()

    monkeypatch.setattr(
        recovery_cli.request, "build_opener", lambda *_: Opener())
    status, body = recovery_cli.call_api(token="fixed-token")
    assert status == 200 and body == b'{"ok":true}'
    assert captured["url"].endswith(
        "/api/campaigns/recover-five-price-single-discount")
    assert captured["token"] == "fixed-token"
    assert captured["timeout"] == 2500
    assert service.validate_recovery_request(
        __import__("json").loads(captured["body"]))


def test_super_recovery_cli_has_fixed_endpoint_and_payload(monkeypatch):
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def read():
            return b'{"ok":true}'

    class Opener:
        @staticmethod
        def open(req, timeout):
            captured["url"] = req.full_url
            captured["token"] = req.get_header("X-api-key")
            captured["body"] = req.data
            captured["timeout"] = timeout
            return Response()

    monkeypatch.setattr(
        super_recovery_cli.request, "build_opener", lambda *_: Opener())
    status, body = super_recovery_cli.call_api(token="fixed-token")
    assert status == 200 and body == b'{"ok":true}'
    assert captured["url"].endswith(
        "/api/campaigns/recover-five-price-super-reduce")
    assert captured["token"] == "fixed-token"
    assert captured["timeout"] == 2500
    assert service.validate_super_recovery_request(
        __import__("json").loads(captured["body"]))


def test_super_recovery_v2_cli_has_fixed_endpoint_and_payload(monkeypatch):
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def read():
            return b'{"ok":true}'

    class Opener:
        @staticmethod
        def open(req, timeout):
            captured["url"] = req.full_url
            captured["body"] = req.data
            captured["timeout"] = timeout
            return Response()

    monkeypatch.setattr(
        super_recovery_v2_cli.request, "build_opener", lambda *_: Opener())
    status, body = super_recovery_v2_cli.call_api(token="fixed-token")
    assert status == 200 and body == b'{"ok":true}'
    assert captured["url"].endswith(
        "/api/campaigns/recover-five-price-super-reduce-v2")
    assert captured["timeout"] == 3600
    assert service.validate_super_recovery_v2_request(
        __import__("json").loads(captured["body"]))


def test_super_recovery_v3_cli_has_fixed_endpoint_and_payload(monkeypatch):
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def read():
            return b'{"ok":true}'

    class Opener:
        @staticmethod
        def open(req, timeout):
            captured["url"] = req.full_url
            captured["body"] = req.data
            captured["timeout"] = timeout
            return Response()

    monkeypatch.setattr(
        super_recovery_v3_cli.request, "build_opener", lambda *_: Opener())
    status, body = super_recovery_v3_cli.call_api(token="fixed-token")
    assert status == 200 and body == b'{"ok":true}'
    assert captured["url"].endswith(
        "/api/campaigns/recover-five-price-super-reduce-v3")
    assert captured["timeout"] == 3600
    assert service.validate_super_recovery_v3_request(
        json.loads(captured["body"]))


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
    assert dependencies.CAMPAIGN_FIVE_PRICE_RECOVERY_PATH in (
        dependencies.CAMPAIGN_PREPARE_SERVICE_PATHS
    )
    assert dependencies.CAMPAIGN_FIVE_PRICE_SUPER_RECOVERY_PATH in (
        dependencies.CAMPAIGN_PREPARE_SERVICE_PATHS
    )
    assert dependencies.CAMPAIGN_FIVE_PRICE_SUPER_RECOVERY_V2_PATH in (
        dependencies.CAMPAIGN_PREPARE_SERVICE_PATHS
    )
    assert dependencies.CAMPAIGN_FIVE_PRICE_SUPER_RECOVERY_V3_PATH in (
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
