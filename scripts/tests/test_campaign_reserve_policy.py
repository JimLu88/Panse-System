import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from campaign_reserve_policy import fixed_basis, inherit_basis, inventory
from campaign_generate_current_files import load_bases
from verify_campaign_frozen_outcomes import CONTRACT, RECEIPT, verify


class ReservePolicyTest(unittest.TestCase):
    def setUp(self):
        self.basis = dict(original='1000', floor='200', source='original-receipt')
        self.row = dict(item='100', sku='101', erp_code='P1', custom=True,
                        classification_verified=True, failed_current_scope=True,
                        basis=self.basis, rotation_needed_verified=True)
        self.lineage = dict(item='100', erp_code='P1', source_sku='101',
                            replacement_sku='102', receipt='saved-platform-receipt', mapping_verified=True)

    def action(self, **changes):
        return inventory([dict(self.row, **changes)])['rows'][0]['action']

    def test_fixed_floor_rejects_rebase_and_nonfinite(self):
        for original, floor in [('1000','40'), ('1000','199.99'), ('NaN','200'), ('Infinity','Infinity')]:
            with self.assertRaises(ValueError):
                fixed_basis(original, floor)

    def test_unknown_is_not_missing(self):
        self.assertEqual(self.action(), 'unknown_reserve_gap')
        self.assertEqual(self.action(basis=None), 'unknown_original_basis')
        self.assertEqual(self.action(rotation_needed_verified=False), 'rotation_need_unknown')

    def test_classification_and_success_scope_protected(self):
        self.assertEqual(self.action(custom=False), 'ordinary_or_classification_needs_decision')
        self.assertEqual(self.action(classification_verified=False), 'ordinary_or_classification_needs_decision')
        self.assertEqual(self.action(failed_current_scope=False), 'outside_current_failed_scope')

    def test_direct_fix_at_floor_precedes_rotation(self):
        self.assertEqual(self.action(verified_feasible_signup_price='200'), 'direct_price_fix_no_rotation_needed')
        self.assertNotEqual(self.action(verified_feasible_signup_price='199.99'), 'direct_price_fix_no_rotation_needed')

    def test_reuse_only_exact_verified_inactive_reserve(self):
        reserve = dict(item='100', erp_code='P1', sku='102', mapping_verified=True,
                       enabled=False, attributes_match=True, usable_verified=True, evidence='page-receipt', stock=100)
        self.assertEqual(self.action(reserves=[reserve]), 'reuse_existing_reserve')
        for change in [dict(item='200'), dict(erp_code='P2'), dict(enabled=True), dict(attributes_match=False), dict(usable_verified=False)]:
            self.assertEqual(self.action(reserves=[dict(reserve, **change)], reserve_inventory_complete=True, reserve_inventory_evidence='page'), 'unknown_reserve_gap')

    def test_confirmed_gap_only_and_no_write_authority(self):
        row = dict(self.row, reserves=[], reserve_inventory_complete=True, reserve_inventory_evidence='current-page')
        before = copy.deepcopy(row)
        result = inventory([row])
        self.assertEqual(result['counts'], {'create_reserve_needed':1})
        self.assertFalse(result['platform_write'])
        self.assertFalse(result['automatic_rotation'])
        self.assertEqual(row, before)

    def test_deduplicated_identity_required(self):
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            inventory([self.row, self.row])
        with self.assertRaisesRegex(ValueError, 'missing_exact'):
            inventory([dict(self.row, erp_code='')])

    def test_floor_origin_survives_multiple_replacements(self):
        first = inherit_basis(self.basis, self.lineage)
        second = inherit_basis(first, dict(self.lineage, source_sku='102', replacement_sku='103'))
        self.assertEqual(second['original'], '1000')
        self.assertEqual(second['floor'], '200')
        self.assertEqual(second['source'], 'original-receipt')
        self.assertNotIn('lineage', self.basis)
        for changes in [dict(erp_code='P2'), dict(source_sku='999'), dict(replacement_sku='101')]:
            with self.assertRaises(ValueError):
                inherit_basis(first, {**self.lineage, 'source_sku':'102', 'replacement_sku':'103', **changes})

    def test_generator_imports_inherited_floor_without_new_preflight(self):
        row = dict(item='100', sku='101', erp_code='P1', fixed_original_record='1000', fixed_floor='200', verified_replacement_lineage=[self.lineage])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'receipt.json'
            path.write_text(json.dumps({'rows':[row]}), encoding='utf-8')
            bases = load_bases([path])
            self.assertEqual(bases[('100','102')]['floor'], '200')
            self.assertEqual(bases[('100','102')]['source'], str(path))
            row['verified_replacement_lineage'][0]['source_sku'] = '999'
            path.write_text(json.dumps({'rows':[row]}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'lineage_source'):
                load_bases([path])
        self.assertEqual(load_bases([]), {})

    def test_new_policy_cannot_silently_be_loosened(self):
        contract = json.loads(CONTRACT.read_text(encoding='utf-8-sig'))
        receipt = json.loads(RECEIPT.read_text(encoding='utf-8-sig'))
        for key in ('ordinary_default_rotation','unknown_is_missing','daily_price_scan','one_time_authorization_is_recurring','replacement_resets_platform_history_guaranteed'):
            changed = copy.deepcopy(contract)
            changed['custom_replacement_policy'][key] = True
            self.assertIn('frozen_rule_changed:custom_replacement_policy', verify(changed, receipt))


if __name__ == '__main__':
    unittest.main()
