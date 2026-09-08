import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from campaign_price_basis import BasisCatalog
from campaign_generate_current_files import load_bases
from enrich_custom_inventory import attach_existing_snapshot, enrich
from campaign_price_snapshot import digest


class BasisTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.row = dict(item="100", sku="101", erp_code="P1", campaign="autumn",
                        custom=True, classification_verified=True, failed_current_scope=True,
                        verified_feasible_signup_price="200", rotation_needed_verified=True)
        self.record = dict(item="100", sku="101", erp_code="P1",
                           fixed_original_record="1000", fixed_floor="200")

    def source(self, name, rows, kind="fixed"):
        path = self.root / name
        path.write_text(json.dumps({"rows": rows}), encoding="utf-8")
        return dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(), kind=kind)

    def test_third_source_not_silently_omitted(self):
        a = self.source("a.json", [])
        b = self.source("b.json", [])
        c = self.source("c.json", [self.record])
        result = enrich([self.row], BasisCatalog([a, b, c]))
        self.assertEqual(result["rows"][0]["basis_status"], "confirmed")
        self.assertEqual(len(result["direct_price_fix"]), 1)
        self.assertEqual(result["manual_handoff"], [])

    def test_equality_is_direct_and_below_is_not(self):
        cat = BasisCatalog([self.source("a.json", [self.record])])
        for price, direct in [("200", True), ("200.01", True), ("199.99", False)]:
            r = enrich([dict(self.row, verified_feasible_signup_price=price)], cat)
            self.assertEqual(bool(r["direct_price_fix"]), direct)

    def test_historical_daily_is_not_fixed(self):
        cat = BasisCatalog([self.source("h.json", [dict(item="100", sku="101", erp_code="P1", daily="1000")], "historical")])
        r = enrich([self.row], cat)["rows"][0]
        self.assertEqual(r["basis_status"], "historical_only")
        self.assertIsNone(r["fixed_original"])
        self.assertEqual(r["action"], "unknown_original_basis")
        self.assertFalse(r["manual_handoff"])

    def test_not_loaded_and_no_record_are_distinct(self):
        self.assertEqual(BasisCatalog().resolve(self.row)["status"], "not_loaded")
        self.assertEqual(BasisCatalog([self.source("a.json", [])]).resolve(self.row)["status"], "no_record_in_loaded_sources")

    def test_conflict_never_last_source_wins(self):
        a = self.source("a.json", [self.record])
        b = self.source("b.json", [dict(self.record, fixed_original_record="1500", fixed_floor="300")])
        r = enrich([self.row], BasisCatalog([a, b]))["rows"][0]
        self.assertEqual(r["basis_status"], "source_conflict")
        self.assertIsNone(r["basis"])
        with self.assertRaisesRegex(ValueError, "conflicting"):
            load_bases([Path(a["path"]), Path(b["path"])])

    def test_numeric_format_does_not_create_conflict(self):
        a = self.source("a.json", [self.record])
        b = self.source("b.json", [dict(self.record, fixed_original_record="1000.00", fixed_floor="200.00")])
        self.assertEqual(BasisCatalog([a, b]).resolve(self.row)["status"], "confirmed")
        self.assertEqual(len(load_bases([Path(a["path"]), Path(b["path"])])), 1)

    def test_real_legacy_alias_schema_supported(self):
        r = dict(item_id="100", sku_id="101", erp_code="P1", fixed_original_record="1000", floor="200")
        src = self.source("third.json", [r])
        self.assertEqual(BasisCatalog([src]).resolve(self.row)["status"], "confirmed")
        self.assertEqual(load_bases([Path(src["path"])])[("100", "101")]["floor"], "200")

    def test_source_identity_mismatch_not_joined_by_name(self):
        src = self.source("a.json", [dict(self.record, erp_code="OTHER")])
        self.assertEqual(BasisCatalog([src]).resolve(self.row)["status"], "source_identity_conflict")

    def test_source_version_mismatch_and_scoped_isolation(self):
        src = self.source("a.json", [self.record])
        src.update(sha256="wrong", scope=[["100", "101"]])
        cat = BasisCatalog([src])
        self.assertEqual(cat.resolve(self.row)["status"], "source_version_mismatch")
        self.assertEqual(cat.resolve(dict(self.row, item="999"))["status"], "no_record_in_loaded_sources")

    def test_price_version_mismatch_does_not_rotate(self):
        cat = BasisCatalog([self.source("a.json", [self.record])])
        r = enrich([dict(self.row, constraint_price_version="old", current_price_version="new")], cat)["rows"][0]
        self.assertEqual(r["action"], "unknown_price_evidence")

    def test_bad_fixed_record_only_blocks_its_row(self):
        src = self.source("a.json", [self.record, dict(self.record, sku="102", fixed_floor="199")])
        cat = BasisCatalog([src])
        self.assertEqual(cat.resolve(self.row)["status"], "confirmed")
        self.assertEqual(cat.resolve(dict(self.row, sku="102"))["status"], "source_conflict")

    def test_success_locked_and_protected_before_manual_handoff(self):
        rows = [dict(self.row, sku=str(i), custom=False, **change) for i, change in enumerate(
            [dict(successful=True), dict(locked=True), dict(protected=True)])]
        result = enrich(rows, BasisCatalog())
        self.assertEqual(result["manual_handoff"], [])
        self.assertTrue(all(r["action"].startswith("skip_") for r in result["rows"]))

    def test_only_ordinary_and_classification_doubt_to_colleague(self):
        rows = [self.row, dict(self.row, sku="102", custom=False),
                dict(self.row, sku="103", classification_verified=False)]
        result = enrich(rows, BasisCatalog())
        self.assertEqual([r["sku"] for r in result["manual_handoff"]], ["102", "103"])

    def test_existing_reserve_reused_not_created(self):
        cat = BasisCatalog([self.source("a.json", [self.record])])
        reserve = dict(item="100", erp_code="P1", sku="102", mapping_verified=True,
                       enabled=False, attributes_match=True, usable_verified=True, evidence="saved", stock=100)
        row = dict(self.row, verified_feasible_signup_price="100", reserves=[reserve],
                   reserve_inventory_complete=True, reserve_inventory_evidence="saved")
        self.assertEqual(enrich([row], cat)["rows"][0]["action"], "reuse_existing_reserve")

    def test_campaign_scopes_separate_and_inputs_preserved(self):
        before = copy.deepcopy(self.row)
        result = enrich([self.row, dict(self.row, campaign="super_reduce")], BasisCatalog())
        self.assertEqual(len(result["rows"]), 2)
        self.assertEqual(self.row, before)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            enrich([self.row, self.row], BasisCatalog())

    def test_success_on_another_campaign_is_not_global_exclusion(self):
        cat = BasisCatalog([self.source("a.json", [self.record])])
        row = dict(self.row, successful_campaigns=["autumn", "super88"])
        result = enrich([row, dict(row, campaign="super_reduce")], cat)
        self.assertEqual(result["rows"][0]["action"], "skip_successful_or_protected")
        self.assertEqual(result["rows"][1]["action"], "direct_price_fix_no_rotation_needed")

    def test_missing_classification_is_not_silently_colleague_work(self):
        with self.assertRaisesRegex(ValueError, "classification"):
            enrich([{"item": "100", "sku": "101", "erp_code": "P1"}], BasisCatalog())

    def test_saved_table_is_not_recreated_and_counts_are_not_rotation_counts(self):
        cat = BasisCatalog([self.source("a.json", [self.record])])
        result = enrich([dict(self.row, saved_pending_readback=True)], cat)
        self.assertEqual(result["scope_count"], 1)
        self.assertEqual(result["direct_actionable_count"], 0)
        self.assertEqual(result["saved_pending_readback_count"], 1)
        self.assertEqual(result["manual_handoff"], [])

    def test_direct_action_never_grants_global_product_mutation(self):
        cat = BasisCatalog([self.source("a.json", [self.record])])
        result = enrich([self.row], cat)["direct_price_fix"][0]
        self.assertTrue(result["change_scope"]["activity_signup_only"])
        for field in ("product_global_price", "stock", "enabled", "sku_identity"):
            self.assertFalse(result["change_scope"][field])

    def test_existing_snapshot_attaches_exact_code_without_fetch(self):
        erp = [dict(item="100", sku="101", code="P1", custom=True)]
        snap = dict(all_erp_rows=erp, resolved_price_version_sha256=digest(erp))
        rows = attach_existing_snapshot([dict(self.row, erp_code=None)], snap)
        self.assertEqual(rows[0]["erp_code"], "P1")
        rows = attach_existing_snapshot([dict(self.row, erp_code="WRONG")], snap)
        self.assertEqual(enrich(rows, BasisCatalog())["rows"][0]["action"], "unknown_business_identity")
        with self.assertRaisesRegex(ValueError, "snapshot_changed"):
            attach_existing_snapshot([self.row], dict(snap, resolved_price_version_sha256="bad"))

    def test_legacy_item_sku_basis_needs_explicit_verified_erp_mapping(self):
        src = self.source("legacy.json", [dict(self.record, erp_code="")])
        cat = BasisCatalog([src])
        self.assertEqual(cat.resolve(self.row)["status"], "source_identity_conflict")
        erp = [dict(item="100", sku="101", code="P1", custom=True)]
        row = attach_existing_snapshot([self.row], dict(all_erp_rows=erp, resolved_price_version_sha256=digest(erp)))[0]
        resolved = cat.resolve(row)
        self.assertEqual(resolved["status"], "confirmed")
        self.assertEqual(resolved["basis"]["erp_code"], "P1")
        self.assertEqual(resolved["basis"]["identity_binding_snapshot_sha256"], digest(erp))


if __name__ == "__main__":
    unittest.main()
