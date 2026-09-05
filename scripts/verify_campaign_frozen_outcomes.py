"""Offline maintenance check. No network, business writes, or signup entry."""
import hashlib
import json
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs/campaign-signup-frozen-contract.json"
RECEIPT = ROOT / "docs/receipts/campaign-49462-price-audit-20260906.json"


def verify(contract, receipt):
    """Return contract/receipt errors without altering either input."""
    errors = []

    def check(condition, label):
        if not condition:
            errors.append(label)

    fixed = {
        "owner": "01｜畔色ERP系统",
        "owner_thread_id": "01a04666-6895-7b40-a07d-c7cfe38d9a02",
        "maintenance_business_execution": False,
        "rule_changes_require_current_direct_user_instruction": True,
        "first_attempt_scope": "all_current_erp_sellable_items",
        "historical_sales_prefilter": False,
        "automatic_retry": False,
        "replay_successful_scope": False,
        "replay_unknown_outcome": False,
        "reuse_proven_same_window_discount": True,
        "preflight_candidate_scan": False,
        "preflight_price_evidence_refresh": False,
        "preflight_r16_r17_loop": False,
        "normal_signup_price": "erp_daily_price",
        "custom_floor_ratio": "0.20",
        "custom_floor_basis": "first_original_custom_price",
        "automatically_rotate_sku": False,
        "reserve_sku": {"enabled": False, "stock": "preserve"},
        "missing_required_installation_detail": "提供简单安装工具",
        "overwrite_valid_installation_detail": False,
        "optional_images_before_signup": False,
        "browser": "edge",
        "close_user_browser": False,
        "failure_report_download_attempts": 1,
        "terminal_wait_seconds_approx": 180,
        "no_progress_report_seconds_max": 600,
        "audit_is_historical_snapshot": True,
        "runtime_short_flow_deployed_by_this_freeze": False,
        "database_mapping_updated_by_this_freeze": False,
        "price_targets": {"super_reduce": "medium_promo", "super88_12_percent": "big_promo", "major_15_percent": "big_promo"},
        "template": {"fresh_per_campaign": True, "dimension": "sku", "reference_data": True, "smart_fill": "none", "preserve_custom_properties": True, "preserve_non_data_parts": True, "preserve_merge_geometry": True},
        "steps": ["download_current_official_template", "generate_two_files_one_price_version", "single_discount_terminal_success", "activity_signup_terminal", "save_result_or_one_failure_report"],
    }
    for key, value in fixed.items():
        check(contract.get(key) == value, "frozen_rule_changed:" + key)
    check(receipt.get("campaign_id") == "49462", "campaign_identity")
    check(receipt.get("united_activity_id") == "49469", "united_activity_identity")
    check(receipt.get("sign_record_id") == "3527841611", "signup_record_identity")
    check(receipt.get("all_prices_verified") is False, "unresolved_prices_must_remain_visible")
    check(receipt.get("no_replay") is True and receipt.get("automatic_retry") is False, "no_replay")
    check(receipt.get("platform_write_this_audit") is False, "no_platform_write")
    check(receipt.get("erp_business_database_write") is False, "no_business_database_write")
    check(receipt.get("runtime_short_flow_implementation_verified") is False, "no_runtime_claim")
    check(receipt.get("routine_signup_thread_id") == fixed["owner_thread_id"], "receipt_owner")
    check(receipt.get("published") == {"sku_rows": 354, "items": 45}, "published_snapshot")
    check(receipt.get("draft") == {"sku_rows": 70, "items": 6}, "draft_snapshot")
    rows = receipt.get("repaired_rows", [])
    check(len(rows) == 228, "repaired_row_count")
    check(len({(row["item"], row["sku"]) for row in rows}) == 228, "unique_sku_identity")
    check(len({row["item"] for row in rows}) == 24, "repaired_item_count")
    matches = missing = differences = 0
    for row in rows:
        check(Decimal(str(row["expected_signup"])) == Decimal(str(row["official_signup"])), "signup_price:" + row["sku"])
        final = row["official_final"]
        if final in (None, ""):
            missing += 1
        elif Decimal(str(final)) == Decimal(str(row["expected_final"])):
            matches += 1
        else:
            differences += 1
    check((matches, missing, differences) == (201, 24, 3), "final_price_classification")
    check(receipt.get("repaired_scope") == {"signup_price_match": 228, "published_setting_rows": 228, "sku_rows": 228, "final_price_match": 201, "custom_final_difference_rows": 3, "final_price_missing": 24, "items": 24}, "repaired_scope_summary")
    check(len(receipt.get("large_final_difference_rows", [])) == 12, "large_difference_preserved")
    check(len(receipt.get("unmapped_rows", [])) == 16, "unmapped_rows_preserved")
    check(len(receipt.get("custom_floor_review", [])) == 2, "original_price_unknown_preserved")
    batches = {entry["operation"]: entry for entry in receipt.get("official_batches", [])}
    check(set(batches) == {"790318164", "793048029", "794292184", "794707209"}, "successful_batch_ids")
    check(batches.get("793048029", {}).get("unique_successful_items") == 6, "partial_batch_not_whole_success")
    return errors


def verify_repository(root=ROOT):
    contract = json.loads((root / CONTRACT.relative_to(ROOT)).read_text(encoding="utf-8-sig"))
    raw = (root / RECEIPT.relative_to(ROOT)).read_bytes()
    receipt = json.loads(raw.decode("utf-8-sig"))
    errors = verify(contract, receipt)
    # Older Windows worktrees may still have CRLF from before the exact-path
    # .gitattributes rule. Compare the immutable Git/LF JSON, not checkout EOLs.
    if hashlib.sha256(raw.replace(b'\r\n', b'\n')).hexdigest() != contract["outcome_receipt_sha256"].lower():
        errors.append("historical_receipt_bytes_changed")
    if contract["outcome_receipt"] != RECEIPT.relative_to(ROOT).as_posix():
        errors.append("receipt_path_changed")
    return errors


if __name__ == "__main__":
    try:
        failures = verify_repository()
    except (OSError, ValueError, KeyError, TypeError, ArithmeticError) as exc:
        failures = ["invalid_local_artifact:" + str(exc)]
    print(json.dumps({"ok": not failures, "mode": "offline_maintenance_only", "platform_write": False, "errors": failures}, ensure_ascii=False))
    raise SystemExit(1 if failures else 0)
