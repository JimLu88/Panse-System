"""Enrich an existing failed-SKU list from an explicit, versioned source manifest.

Replaces the retired two-source/count==11 output scripts. No API/browser calls;
this is failure-report processing, never a first-signup prerequisite.
"""
import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path

from campaign_price_basis import BasisCatalog
from campaign_reserve_policy import decide
from campaign_price_snapshot import digest


def attach_existing_snapshot(rows, snapshot):
    """Use an already obtained local snapshot, never trigger fresh ERP retrieval."""
    erp = snapshot["all_erp_rows"]
    if digest(erp) != snapshot["resolved_price_version_sha256"]:
        raise ValueError("price_snapshot_changed")
    result = []
    for source in rows:
        row = deepcopy(source)
        matches = []
        for record in erp:
            items = {str(x) for x in [record.get("item"), record.get("product_item_id"), *(record.get("product_alt_item_ids") or [])] if x}
            skus = {str(x) for x in [record.get("sku"), *(record.get("alt") or [])] if x}
            if row["item"] in items and row["sku"] in skus:
                matches.append(record)
        if len(matches) != 1 or (row.get("erp_code") and row["erp_code"] != matches[0]["code"]):
            row["identity_error"] = "snapshot_mapping_missing_ambiguous_or_conflicting"
        else:
            row["erp_code"] = matches[0]["code"]
            row["mapping_price_version"] = snapshot["resolved_price_version_sha256"]
            if matches[0].get("custom") != row.get("custom"):
                row["classification_verified"] = False
        result.append(row)
    return result


def enrich(rows, catalog):
    result, seen = [], set()
    for source in rows:
        row = deepcopy(source)
        if not all(isinstance(row.get(k), bool) for k in ("custom", "classification_verified", "failed_current_scope")):
            raise ValueError("explicit_row_classification_and_scope_required")
        if not all(isinstance(row.get(k), str) and row[k] for k in ("campaign", "item", "sku")):
            raise ValueError("exact_campaign_business_identity_required")
        if not isinstance(row.get("erp_code"), str) or not row["erp_code"]:
            row["identity_error"] = "erp_code_not_loaded"
            row["erp_code"] = ""
        key = row.get("campaign"), row["item"], row["sku"]
        if key in seen:
            raise ValueError("duplicate_campaign_sku")
        seen.add(key)
        resolution = catalog.resolve(row)
        row.update(basis_status=resolution["status"], basis=resolution["basis"],
                   basis_resolution=resolution, fixed_original=None, fixed_floor=None)
        if row["basis"]:
            row.update(fixed_original=row["basis"]["original"], fixed_floor=row["basis"]["floor"])
        # A changed price snapshot invalidates only this row's feasible-price claim.
        expected, actual = row.get("constraint_price_version"), row.get("current_price_version")
        if expected is not None and expected != actual:
            row["verified_feasible_signup_price"] = None
            row["price_evidence_error"] = "price_version_mismatch"
        try:
            row["action"] = decide(row)
        except (ValueError, KeyError, TypeError) as exc:
            row.update(action="unknown_invalid_row_evidence", evidence_error=str(exc))
        row["manual_handoff"] = row["action"] == "ordinary_or_classification_needs_decision"
        row["change_scope"] = dict(campaign=row["campaign"], activity_signup_only=True,
                                   exact_discount_window_only=True, product_global_price=False,
                                   stock=False, enabled=False, sku_identity=False)
        result.append(row)
    return dict(schema=1, mode="offline_failed_scope_only", platform_write=False,
                automatic_rotation=False, rows=result,
                scope_count=len({(r["item"], r["sku"]) for r in result}),
                direct_actionable_count=sum(r["action"] == "direct_price_fix_no_rotation_needed" for r in result),
                saved_pending_readback_count=len({(r["item"], r["sku"]) for r in result if r.get("saved_pending_readback")}),
                action_counts=dict(Counter(r["action"] for r in result)),
                basis_counts=dict(Counter(r["basis_status"] for r in result)),
                manual_handoff=[r for r in result if r["manual_handoff"]],
                direct_price_fix=[r for r in result if r["action"] == "direct_price_fix_no_rotation_needed"],
                rotation_candidates_are_mandatory=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("--basis-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, help="Existing local resolved snapshot; never fetched")
    args = parser.parse_args()
    data = json.loads(args.inventory.read_text(encoding="utf-8-sig"))
    rows = []
    for row in data["rows"]:
        if row.get("campaign"):
            rows.append(row)
        else:
            # Extractor stores one identity with multiple official campaigns.
            # Never merge those campaign constraints or success states together.
            for campaign in row.get("campaigns", []):
                evidence = [e for e in row.get("evidence", []) if e.get("campaign") == campaign]
                rows.append(dict(row, campaign=campaign, evidence=evidence,
                                 evidence_scope_unknown=any(not e.get("campaign") for e in row.get("evidence", []))))
            if not row.get("campaigns"):
                raise ValueError("missing_campaign_scope")
    if args.snapshot:
        rows = attach_existing_snapshot(rows, json.loads(args.snapshot.read_text(encoding="utf-8-sig")))
    result = enrich(rows, BasisCatalog.from_manifest(args.basis_manifest))
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(dict(output=str(args.output), counts=result["action_counts"], basis=result["basis_counts"]), ensure_ascii=False))


if __name__ == "__main__":
    main()
