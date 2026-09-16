import hashlib
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from campaign_template_failure_supplement import supplement


class SupplementTests(unittest.TestCase):
    def setUp(self):
        self.raw=b'unchanged'; self.sha=hashlib.sha256(self.raw).hexdigest()
        self.rows={2:{'A':'商品ID','E':'SKUID','F':'SKU名称','H':'最低标价'},
                   4:{'A':'805268708396','E':'5503342081613','F':'颜色定制（咨询客服）','H':'1125.00'}}
        self.error=dict(item='805268708396',sku='',kind='unknown',batch='831931699',
            parse_issue='unparsed_or_incomplete_official_failure',terminal='failed',
            message='原始原因 您的sku：颜色定制（咨询客服） 在管',official_evidence={'sha256':'old'})
        self.evidence={'path':'official.xlsx','submitted_prices':{('805268708396','5503342081613'):'1500.00'}}

    def run_it(self,errors=None):
        with patch('campaign_template_failure_supplement.read_rows',return_value=self.rows):
            return supplement(errors or [self.error],self.raw,expected_sha=self.sha,evidence=self.evidence)

    def test_exact_tail_uses_reference_keeps_raw(self):
        row=self.run_it()[0]
        self.assertEqual((row['sku'],row['official_cap']),('5503342081613','1125.00'))
        self.assertEqual(row['message'],self.error['message'])
        self.assertEqual(row['supplemental_evidence']['cell'],'H4')
        self.assertEqual(self.error['kind'],'unknown')

    def test_other_failure_preserved(self):
        other=dict(self.error,parse_issue='other_unknown')
        self.assertEqual(self.run_it([self.error,other])[1],other)

    def test_incomplete_name_not_guessed(self):
        self.error['message']='您的sku：颜色定制 在管'
        with self.assertRaisesRegex(ValueError,'not_unique'):self.run_it()

    def test_duplicate_name_rejected(self):
        self.rows[5]=dict(self.rows[4],E='1234567891234')
        with self.assertRaisesRegex(ValueError,'not_unique'):self.run_it()

    def test_missing_cap_rejected(self):
        self.rows[4]['H']=''
        with self.assertRaisesRegex(ValueError,'not_unique'):self.run_it()

    def test_missing_submission_rejected(self):
        self.evidence['submitted_prices']={}
        with self.assertRaisesRegex(ValueError,'failed_submission'):self.run_it()

    def test_bad_tail_not_interpreted(self):
        self.error['message']='您的sku：颜色定制（咨询客服） 在管控期标价为'
        with self.assertRaisesRegex(ValueError,'no_proven'):self.run_it()

    def test_truncated_at_guankong_is_exact(self):
        self.error['message']='您的sku：颜色定制（咨询客服） 在管控'
        self.assertEqual(self.run_it()[0]['official_cap'],'1125.00')

    def test_other_incomplete_price_clause_remains_unknown(self):
        self.error['message']='您的sku：另一规格 在管控期标价为?；您的sku：颜色定制（咨询客服） 在管控'
        with self.assertRaisesRegex(ValueError,'no_proven'):self.run_it()

    def test_hash_mismatch_rejected(self):
        self.sha='0'*64
        with self.assertRaisesRegex(ValueError,'changed'):self.run_it()


if __name__=='__main__':unittest.main()
