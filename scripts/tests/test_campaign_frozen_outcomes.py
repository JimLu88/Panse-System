import copy
import importlib.util
import json
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "verify_campaign_frozen_outcomes.py"
SPEC = importlib.util.spec_from_file_location("campaign_freeze_check", SOURCE)
CHECK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECK)


class FrozenOutcomesTest(unittest.TestCase):
    def setUp(self):
        self.contract = json.loads(CHECK.CONTRACT.read_text(encoding="utf-8-sig"))
        self.receipt = json.loads(CHECK.RECEIPT.read_text(encoding="utf-8-sig"))

    def test_repository_and_receipt_hash(self):
        self.assertEqual(CHECK.verify_repository(), [])

    def test_user_collaboration_is_frozen(self):
        self.contract["collaboration"]["mode"] = "unattended"
        self.assertIn("frozen_rule_changed:collaboration", CHECK.verify(self.contract, self.receipt))

    def test_no_mutation(self):
        before = copy.deepcopy((self.contract, self.receipt))
        CHECK.verify(self.contract, self.receipt)
        self.assertEqual(before, (self.contract, self.receipt))

    def test_failed_52_scope_cannot_be_replayed_or_called_success(self):
        scope = json.loads((CHECK.ROOT / 'docs/campaign-draft52-continuation.json').read_text(encoding='utf-8-sig'))
        terminal = json.loads((CHECK.ROOT / 'docs/receipts/campaign-draft52-full-sku-terminal-snapshot-20260906.json').read_text(encoding='utf-8-sig'))
        self.assertIs(scope['submission_checkpoint']['new_generation_allowed'], False)
        self.assertEqual(terminal['official_success_items'], 0)
        self.assertEqual(terminal['official_failed_items'], 6)
        self.assertEqual(scope['submission_checkpoint']['official_terminal_at_checkpoint']['batch_id'], terminal['batch_id'])
        self.assertEqual({(i, sku) for i, skus in terminal['missing_custom_skus'].items() for sku in skus}, {(r['item_id'], r['sku_id']) for r in scope['blocked_custom']})

    def test_owner_not_maintenance(self):
        self.contract["owner"] = "02"
        self.assertIn("frozen_rule_changed:owner", CHECK.verify(self.contract, self.receipt))

    def test_preflight_cannot_return(self):
        for key in ("preflight_candidate_scan", "preflight_price_evidence_refresh", "preflight_r16_r17_loop", "historical_sales_prefilter"):
            with self.subTest(key=key):
                changed = copy.deepcopy(self.contract)
                changed[key] = True
                self.assertIn("frozen_rule_changed:" + key, CHECK.verify(changed, self.receipt))

    def test_no_replay_or_automatic_rotation(self):
        for key in ("automatic_retry", "replay_successful_scope", "replay_unknown_outcome", "automatically_rotate_sku"):
            changed = copy.deepcopy(self.contract)
            changed[key] = True
            self.assertIn("frozen_rule_changed:" + key, CHECK.verify(changed, self.receipt))

    def test_original_floor_not_reduced_basis(self):
        self.contract["custom_floor_basis"] = "latest_reduced_price"
        self.assertIn("frozen_rule_changed:custom_floor_basis", CHECK.verify(self.contract, self.receipt))

    def test_stock_zero_not_reserve_policy(self):
        self.contract["reserve_sku"] = {"enabled": True, "stock": 0}
        self.assertIn("frozen_rule_changed:reserve_sku", CHECK.verify(self.contract, self.receipt))

    def test_template_geometry_preserved(self):
        self.contract["template"]["preserve_merge_geometry"] = False
        self.assertIn("frozen_rule_changed:template", CHECK.verify(self.contract, self.receipt))

    def test_duplicate_sku_rejected(self):
        self.receipt["repaired_rows"][1] = copy.deepcopy(self.receipt["repaired_rows"][0])
        self.assertIn("unique_sku_identity", CHECK.verify(self.contract, self.receipt))

    def test_signup_price_corruption_rejected(self):
        self.receipt["repaired_rows"][0]["official_signup"] = "0"
        self.assertTrue(any(x.startswith("signup_price:") for x in CHECK.verify(self.contract, self.receipt)))

    def test_blank_final_not_zero(self):
        row = next(row for row in self.receipt["repaired_rows"] if row["official_final"] in (None, ""))
        row["official_final"] = "0"
        self.assertIn("final_price_classification", CHECK.verify(self.contract, self.receipt))

    def test_cannot_claim_all_prices_verified(self):
        self.receipt["all_prices_verified"] = True
        self.assertIn("unresolved_prices_must_remain_visible", CHECK.verify(self.contract, self.receipt))

    def test_partial_batch_not_nine_successes(self):
        next(b for b in self.receipt["official_batches"] if b["operation"] == "793048029")["unique_successful_items"] = 9
        self.assertIn("partial_batch_not_whole_success", CHECK.verify(self.contract, self.receipt))

    def test_no_deployment_claim(self):
        self.contract["runtime_short_flow_deployed_by_this_freeze"] = True
        self.assertIn("frozen_rule_changed:runtime_short_flow_deployed_by_this_freeze", CHECK.verify(self.contract, self.receipt))


if __name__ == "__main__":
    unittest.main()
