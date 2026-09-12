from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from campaign_feedback_normalization import normalize_errors
from campaign_continuous_policy import classify


class NormalizeTests(unittest.TestCase):
    def normalize(self, errors, *, custom=False, original='500', daily='100', price='100', deduct='20'):
        errors=[dict(item='1',sku='11',batch='123',terminal='failed',official_evidence='file-hash',
                     message='official',submitted_price=price,**e) for e in errors]
        return normalize_errors({'errors': errors},
            submitted_rows=[dict(item='1',sku='11',erp_code='CODE',activity_price=price)],
            erp_rows=[dict(code='CODE',custom=custom,daily=daily,big_target='68',medium_target='70')],
            fixed_bases={('1','11'):dict(original=original,source_sha256='basis-hash')},
            actual_discounts=[dict(item='1',sku='11',deduct=deduct,target='big',verified_readback=True,evidence='actual')],rate='.12')

    def test_small_coupon_repair_uses_fixed_target(self):
        row=self.normalize([dict(kind='coupon_price',official_cap='67',observed_final='68')])[0]
        self.assertEqual(classify(row)['action'],'repair')
        self.assertEqual(row['proposed_deduct'],'21')
        self.assertEqual(row['erp_final_target'],'68')

    def test_combines_constraints_before_deciding(self):
        rows=self.normalize([dict(kind='coupon_price',official_cap='67',observed_final='68'),
                             dict(kind='list_price',official_cap='77')])
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['feasible_final_price'],'65')
        self.assertEqual(classify(rows[0])['action'],'rotation_approval')

    def test_exact_two_and_not_two_each_round(self):
        for cap,action in [('66','repair'),('65.99','rotation_approval')]:
            row=self.normalize([dict(kind='coupon_price',official_cap=cap,observed_final='67')],deduct='21')[0]
            self.assertEqual(classify(row)['action'],action)

    def test_custom_floor_not_rebased(self):
        for cap,action in [('88','repair'),('87.99','rotation_approval')]:
            row=self.normalize([dict(kind='coupon_price',official_cap=cap,observed_final='100')],custom=True,original='500',daily='300',price='200')[0]
            self.assertEqual(classify(row)['action'],action)

    def test_wrong_own_price_corrected_without_changing_daily(self):
        row=self.normalize([dict(kind='list_price',official_cap='75')],price='75')[0]
        self.assertEqual(classify(row)['repair'], {'kind':'file_price','price':'100'})

    def test_unexplained_stacking_not_guessed(self):
        row=self.normalize([dict(kind='coupon_price',official_cap='66',observed_final='50')])[0]
        self.assertEqual(classify(row)['action'],'manual')

    def test_small_observed_difference_must_fit_both_before_and_after_bounds(self):
        row=self.normalize([dict(kind='coupon_price',official_cap='67.4',observed_final='67.5')])[0]
        self.assertEqual(classify(row)['action'],'repair')
        self.assertEqual(row['proposed_deduct'],'20.6')
        self.assertTrue(row['bounded_observation_adjustment'])
        # Computed final is 66, but the observed post-change final would be
        # 65.5: outside the same fixed target of 68, so no automatic repair.
        row=self.normalize([dict(kind='coupon_price',official_cap='66',observed_final='67.5')])[0]
        self.assertEqual(classify(row)['action'],'manual')
