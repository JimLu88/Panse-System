from decimal import Decimal

from app.dependencies import (
    CAMPAIGN_PLAN7_SMALL_PROMO_CORRECTION_PATH,
    CAMPAIGN_PREPARE_SERVICE_PATHS,
)
from app.services import campaign_plan7_small_promo_correction_service as service


def test_manifest_is_exactly_twenty_normal_skus():
    rows = service.manifest_rows()

    assert len(rows) == 20
    assert {row["item_id"] for row in rows} == {
        "1036273574687", "1074244132390"}
    assert {item: sum(row["item_id"] == item for row in rows) for item in {
        "1036273574687", "1074244132390"}} == {
        "1036273574687": 8, "1074244132390": 12}
    assert service.FORBIDDEN_SKU_ID not in {row["sku_id"] for row in rows}
    assert service._canonical_sha(rows) == service.MANIFEST_SHA256
    assert service._scope_sha("current") == service.CURRENT_SCOPE_SHA256
    assert service._scope_sha("target") == service.TARGET_SCOPE_SHA256


def test_every_target_moves_mid_buyer_to_small_promo_only():
    for row in service.manifest_rows():
        assert (Decimal(row["target_deduct"])
                - Decimal(row["current_deduct"])) == (
                    Decimal(row["mid_buyer_price"])
                    - Decimal(row["small_promo"]))
        assert Decimal(row["mid_buyer_price"]) > Decimal(row["small_promo"])


def test_fixed_request_and_machine_path_are_registered():
    payload = service.request_payload()

    assert service.validate_request(payload)
    assert not service.validate_request({**payload, "target_activity_id": "143939511827"})
    assert CAMPAIGN_PLAN7_SMALL_PROMO_CORRECTION_PATH in CAMPAIGN_PREPARE_SERVICE_PATHS


def test_terminal_requires_twenty_verified_rows_and_write_boundary():
    exact = {
        "ok": True, "submitted": True,
        "activity_id": service.TARGET_ACTIVITY_ID,
        "official_terminal": {
            "state": "complete", "ok": 20, "failed": 0,
            "source": "exact_activity_editor_readback",
        },
        "execution_boundary": {"platform_write": True},
    }

    assert service._terminal_exact(exact)
    assert not service._terminal_exact({
        **exact, "official_terminal": {**exact["official_terminal"], "failed": 1}})


def test_readback_rejects_wrong_activity_or_amount():
    rows = [{
        **row, "actual_deduct": row["expected_deduct"],
        "classification": "correct_effective", "status": "进行中",
        "activity_ids": [service.TARGET_ACTIVITY_ID],
    } for row in service._scope("target")]
    activities = [{
        "activity_id": activity_id, "identity_readable": True,
        "status": "进行中",
        "row_text": f"{service.START_AT} {service.END_AT}",
    } for activity_id in service.ACTIVITY_IDS]
    result = {"ok": True, "rows": rows, "activity_rows": activities,
              "execution_boundary": {"platform_write": False}}

    assert service._validate_readback(result, kind="target") is None
    rows[0]["activity_ids"] = ["143939511827"]
    assert service._validate_readback(result, kind="target")["error"] == (
        "plan7_small_promo_platform_state_drift")
