import copy
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from campaign_continuous_policy import (
    ENTRY, activity_identity, classify, classify_items, discovery_due,
    fingerprint, load_rules, signup_scope,
)
from campaign_continuous_flow import Blocked, REQUIRED_CAPABILITIES, Store, run

PAGE = {'entry': ENTRY, 'url': 'https://myseller.taobao.com/activity?campaignId=123',
        'shop_id': 'shop-a', 'campaign_id': '123', 'phase_id': '456', 'title': '活动',
        'start': '2026-09-28 00:00:00', 'end': '2026-10-01 23:59:59',
        'official_rate': '.12', 'page_evidence': 'test-page'}


def error(**values):
    return dict({'item': '2', 'sku': '20', 'terminal': 'failed', 'batch': 'B1',
                 'official_evidence': 'test-report', 'message': '价格要求',
                 'custom': False, 'kind': 'coupon_price', 'erp_daily': '100',
                 'submitted_price': '100', 'erp_final_target': '80',
                 'feasible_final_price': '79'}, **values)


class PolicyTests(unittest.TestCase):
    def test_contract_fixed(self):
        rules = load_rules()
        self.assertEqual(rules['official_entry'], ENTRY)
        self.assertFalse(rules['preflight'])
        self.assertTrue(rules['rotation_requires_current_human_confirmation'])

    def test_intersection_not_union_or_sales_filter(self):
        rows = [{'item': '1', 'on_sale': True}, {'item': '2', 'on_sale': False},
                {'item': '3', 'on_sale': True}]
        self.assertEqual(signup_scope(['1', '2', '4'], rows, complete=True,
                                     observed_item_count=3), ['1'])

    def test_incomplete_scope_never_means_empty_store(self):
        for rows, complete, count in [([], False, 0), ([], True, 4),
                 ([{'item': '1', 'on_sale': None}], True, 1),
                 ([{'item': '1', 'on_sale': 1}], True, 1)]:
            with self.subTest(rows=rows, complete=complete, count=count), self.assertRaises(ValueError):
                signup_scope(['1'], rows, complete=complete, observed_item_count=count)

    def test_real_link_and_shop(self):
        identity = activity_identity(PAGE, expected_shop='shop-a', observed_links=[PAGE['url']])
        self.assertEqual(len(identity), 64)
        for changed in [{'entry': 'https://example.org'}, {'shop_id': 'other'},
                        {'url': 'https://taobao.com.evil.org/x'}, {'page_evidence': ''}]:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                activity_identity(dict(PAGE, **changed), expected_shop='shop-a', observed_links=[PAGE['url']])

    def test_newness_ignores_title_but_not_window(self):
        def key(page):
            return activity_identity(page, expected_shop='shop-a', observed_links=[PAGE['url']])
        self.assertEqual(key(PAGE), key(dict(PAGE, title='改名')))
        self.assertNotEqual(key(PAGE), key(dict(PAGE, end='2026-10-02 23:59:59')))

    def test_three_day_clock_and_known_deadline(self):
        now = datetime(2026, 9, 11, 12)
        self.assertFalse(discovery_due(now, now + timedelta(hours=71)))
        self.assertTrue(discovery_due(now, now + timedelta(hours=72)))
        self.assertTrue(discovery_due(now, now + timedelta(hours=1), now + timedelta(minutes=30)))
        self.assertFalse(discovery_due(now, now + timedelta(hours=1), now - timedelta(minutes=30)))

    def test_plus_minus_two_inclusive_never_cumulative(self):
        for final in ('78', '79.99', '80', '82'):
            self.assertEqual(classify(error(feasible_final_price=final))['action'], 'repair')
        for final in ('77.99', '82.01'):
            self.assertEqual(classify(error(feasible_final_price=final))['action'], 'rotation_approval')
        self.assertEqual(classify(error(feasible_final_price='77', previous_round_target='79'))['action'],
                         'rotation_approval')

    def test_ordinary_file_error_vs_platform_limit(self):
        self.assertEqual(classify(error(submitted_price='90'))['repair']['price'], '100')
        self.assertEqual(classify(error(kind='signup_not_daily'))['action'], 'manual')

    def test_exact_fractional_cent_custom_floor(self):
        e = error(custom=True, kind='custom_price', fixed_original='541.67', erp_daily='541.67',
                  submitted_price='541.67', fixed_basis_evidence='user-established-baseline',
                  feasible_signup_price='108.34')
        self.assertEqual(classify(e)['action'], 'repair')
        self.assertEqual(classify(e)['repair']['exact_floor'], '108.3340')
        self.assertEqual(classify(dict(e, feasible_signup_price='108.33'))['action'], 'rotation_approval')
        self.assertEqual(classify(dict(e, fixed_basis_evidence=None))['action'], 'manual')

    def test_unknown_is_not_failure_or_price_or_rotation(self):
        for values in [{'terminal': 'unknown'}, {'custom': None}, {'batch': ''},
                       {'feasible_final_price': None}, {'feasible_final_price': 'NaN'},
                       {'official_evidence': ''}]:
            with self.subTest(values=values):
                self.assertEqual(classify(error(**values))['action'], 'manual')

    def test_mapping_requires_unique_exact_verified_export(self):
        e = error(kind='mapping', full_official_export_verified=True,
                  verified_mapping_candidates=[{'item': '2', 'sku': '20', 'erp_code': 'X'}])
        self.assertEqual(classify(e)['action'], 'repair')
        e['verified_mapping_candidates'].append({'item': '2', 'sku': '21', 'erp_code': 'X'})
        self.assertEqual(classify(e)['action'], 'manual')

    def test_whole_item_grouping_and_current_no_sales(self):
        repairs, exceptions = classify_items([error(), error(sku='21', feasible_final_price='70'),
                                              error(item='3', sku='30')])
        self.assertEqual(set(repairs), {'3'})
        self.assertEqual(set(exceptions), {'2'})
        self.assertEqual(classify(error(kind='no_sales'))['action'], 'not_eligible_this_campaign')


class FakeWebAgent:
    """Offline only. Never registered as a real Web-Agent adapter."""
    def __init__(self, *, permanent_error=False, timeout_at=None, discount_failure=False):
        self.calls = []
        self.signup_count = 0
        self.permanent_error = permanent_error
        self.timeout_at = timeout_at
        self.discount_failure = discount_failure

    def capabilities(self):
        return REQUIRED_CAPABILITIES

    def execute(self, step, action_id, payload):
        self.calls.append((step, copy.deepcopy(payload)))
        if step == self.timeout_at:
            raise TimeoutError('test timeout after potential write')
        out = {'status': 'terminal', 'action_id': action_id, 'request_sha': fingerprint(payload),
               'evidence': 'offline-test-only'}
        items = payload.get('items', [])
        if step == 'scope':
            out.update(erp_sellable=['1', '2', '3'],
                platform_rows=[{'item': i, 'on_sale': i != '3'} for i in ['1', '2', '3']],
                complete=True, observed_item_count=3, price_version='fixed-v1',
                prior_outcomes={}, prior_outcomes_evidence='offline-empty-old-authority')
        elif step == 'generate':
            out.update(items=items, validated_rule_sha=payload['rule_sha'], price_version='fixed-v1',
                       full_active_skus=True, file_sha=fingerprint(items),
                       discount_items=items if payload['round'] == 0 else payload.get('required_discount_items', []))
        elif step == 'discount':
            prior_discount = sum(s == 'discount' for s, _p in self.calls)
            out.update(batch='D1', items=[{'item': i, 'outcome': 'failed' if
                self.discount_failure and i == '2' and prior_discount == 1 else 'success'} for i in items])
        elif step == 'verify_discount_window':
            out.update(start=PAGE['start'], end=PAGE['end'], all_correct=True, items=items)
        elif step == 'signup':
            self.signup_count += 1
            out.update(batch='B' + str(self.signup_count), items=[{'item': i,
                'outcome': 'failed' if i == '2' and (self.signup_count == 1 or self.permanent_error)
                else 'success'} for i in items])
        elif step == 'report':
            out.update(errors=[error(batch=payload['batch'])])
        elif step == 'repair':
            out.update(batch='R1', items=[{'item': i, 'outcome': 'success', 'changed': True} for i in items])
        return out


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'isolated.sqlite3'
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def run_flow(self, driver):
        return run(self.store, driver, PAGE, expected_shop='shop-a', observed_links=[PAGE['url']])

    def test_full_flow_repairs_only_failed_then_finishes(self):
        driver = FakeWebAgent()
        out = self.run_flow(driver)
        self.assertTrue(out['all_signed_up'])
        self.assertEqual(set(out['success']), {'1', '2'})
        self.assertEqual([p['items'] for s, p in driver.calls if s == 'signup'], [['1', '2'], ['2']])
        self.assertEqual(sum(s == 'template' for s, p in driver.calls), 1)
        self.assertEqual(sum(s == 'discount' for s, p in driver.calls), 1)
        self.assertEqual([s for s, p in driver.calls][:6],
                         ['scope', 'template', 'generate', 'discount', 'verify_discount_window', 'signup'])
        calls = len(driver.calls)
        self.assertTrue(self.run_flow(driver)['all_signed_up'])
        self.assertEqual(len(driver.calls), calls)

    def test_repeated_correction_is_final_exception_not_endless_loop(self):
        driver = FakeWebAgent(permanent_error=True)
        out = self.run_flow(driver)
        self.assertEqual(out['status'], 'complete')
        self.assertFalse(out['all_signed_up'])
        self.assertEqual(set(out['exceptions']), {'2'})
        self.assertEqual(driver.signup_count, 2)

    def test_unknown_persists_across_restart_no_resubmit(self):
        driver = FakeWebAgent(timeout_at='signup')
        first = self.run_flow(driver)
        self.assertEqual(first['status'], 'blocked')
        self.store.close()
        self.store = Store(self.path)
        resumed = FakeWebAgent()
        second = self.run_flow(resumed)
        self.assertEqual(second['status'], 'blocked')
        self.assertEqual(resumed.calls, [])

    def test_capability_missing_never_runs_legacy(self):
        driver = FakeWebAgent()
        driver.capabilities = lambda: set()
        out = self.run_flow(driver)
        self.assertFalse(out['platform_write'])
        self.assertEqual(driver.calls, [])

    def test_discount_failure_does_not_submit_full_file_or_wrong_scope(self):
        driver = FakeWebAgent(discount_failure=True)
        out = self.run_flow(driver)
        self.assertTrue(out['all_signed_up'])
        signup = [p for s, p in driver.calls if s == 'signup'][0]
        self.assertEqual(signup['items'], ['1'])
        self.assertEqual(signup['bundle']['items'], ['1'])
        self.assertEqual([p['items'] for s, p in driver.calls if s == 'discount'], [['1', '2'], ['2']])

    def test_unknown_write_recovery_uses_readonly_receipt_without_reupload(self):
        driver = FakeWebAgent(timeout_at='signup')
        out = self.run_flow(driver)
        row = self.store.db.execute("SELECT id,payload_sha FROM continuous_campaign_actions WHERE step='signup'").fetchone()
        receipt = {'status': 'terminal', 'action_id': row[0], 'request_sha': row[1],
                   'reconciled_readonly': True, 'evidence': 'offline-reconciliation-only', 'batch': 'KNOWN',
                   'items': [{'item': i, 'outcome': 'success'} for i in ['1', '2']]}
        self.store.recover(row[0], receipt)
        resumed = FakeWebAgent()
        self.assertTrue(self.run_flow(resumed)['all_signed_up'])
        self.assertEqual(resumed.calls, [])
        with self.assertRaises(Blocked):
            self.store.recover(row[0], dict(receipt, batch='CHANGED'))

    def test_recovery_rejects_wrong_payload_and_read_retry_of_write(self):
        self.run_flow(FakeWebAgent(timeout_at='signup'))
        row = self.store.db.execute("SELECT id,payload_sha FROM continuous_campaign_actions WHERE step='signup'").fetchone()
        with self.assertRaises(Blocked):
            self.store.allow_read_retry(row[0])
        with self.assertRaises(Blocked):
            self.store.recover(row[0], {'status': 'terminal', 'action_id': row[0], 'request_sha': 'wrong',
                                      'evidence': 'test', 'reconciled_readonly': True})

    def test_interrupted_read_can_resume_after_explicit_recovery(self):
        self.run_flow(FakeWebAgent(timeout_at='scope'))
        action = self.store.db.execute("SELECT id FROM continuous_campaign_actions WHERE step='scope'").fetchone()[0]
        self.store.allow_read_retry(action)
        self.assertTrue(self.run_flow(FakeWebAgent())['all_signed_up'])

    def test_imported_old_failure_is_repaired_not_replayed_unchanged(self):
        driver = FakeWebAgent()
        original = driver.execute
        def with_history(step, action, payload):
            result = original(step, action, payload)
            if step == 'scope':
                result.update(prior_outcomes={'1': 'success', '2': 'failed'},
                    prior_failures={'2': {'batch': 'OLD', 'errors': [error(batch='OLD')]}})
            return result
        driver.execute = with_history
        self.run_flow(driver)
        steps = [s for s, _p in driver.calls]
        self.assertEqual(steps[:2], ['scope', 'repair'])
        self.assertTrue(all(p['items'] == ['2'] for s, p in driver.calls if s == 'signup'))

    def test_concurrent_owner_blocked(self):
        run_id = self.store.start('identity', 'rule')
        token = self.store.lock(run_id)
        other = Store(self.path)
        try:
            with self.assertRaises(Blocked):
                other.lock(run_id)
        finally:
            other.close()
        self.store.unlock(run_id, token)

    def test_same_shop_different_campaign_still_single_writer(self):
        first = self.store.start('first', 'rule')
        second = self.store.start('second', 'rule')
        token = self.store.lock(first, 'same-shop')
        try:
            with self.assertRaises(Blocked):
                self.store.lock(second, 'same-shop')
        finally:
            self.store.unlock(first, token)

    def test_bad_history_proof_cannot_skip_replay_protection_on_resume(self):
        driver = FakeWebAgent()
        execute = driver.execute
        def corrupt(step, action_id, payload):
            out = execute(step, action_id, payload)
            if step == 'scope':
                out.pop('prior_outcomes_evidence')
            return out
        driver.execute = corrupt
        self.assertEqual(self.run_flow(driver)['status'], 'blocked')
        self.assertEqual(self.run_flow(driver)['status'], 'blocked')
        self.assertEqual([s for s, p in driver.calls], ['scope'])


if __name__ == '__main__':
    unittest.main()
