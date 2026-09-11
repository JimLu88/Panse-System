from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from campaign_discount_readback import verify
from campaign_continuous_policy import fingerprint


class ReadbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.path=self.root/'readback.json'
        self.args=dict(read_request_id='a'*64,shop='test',start='2026-09-11 00:00:00',
            end='2026-09-12 23:59:59',expected_rows=[dict(offer_id='1',item='2',sku='3',deduct='10.00')],roots=[self.root])
        self.result=dict(state='readback',read_request_id='a'*64,shop_name='test',platform_write=False,
            price_window={'start':self.args['start'],'end':self.args['end']},rows=[dict(item='2',values={'3':'10.00'},
                window=dict(offer_id='1',start=self.args['start'],end=self.args['end']))])
        self.job=dict(operation='discount_readback',state='finished',job_id=fingerprint(['discount_readback','test','a'*64]))

    def run_read(self):
        self.path.write_text(json.dumps(self.result),encoding='utf-8')
        return verify(dict(self.job,result=dict(self.result,evidence_path=str(self.path))),**self.args)

    def test_exact_observed_amount_and_window_succeed(self):
        self.assertTrue(self.run_read()['all_correct'])

    def test_zero_discount_is_valid_but_negative_and_write_receipts_are_not(self):
        self.args['expected_rows'][0]['deduct']='0.00'
        self.result['rows'][0]['values']['3']='0'
        self.assertTrue(self.run_read()['all_correct'])
        self.result['rows'][0]['values']['3']='-0.01'
        with self.assertRaises(ValueError):self.run_read()
        self.result['rows'][0]['values']['3']='0'
        self.result['platform_write']=True
        with self.assertRaises(ValueError):self.run_read()

    def test_difference_is_reported_not_implicitly_fixed(self):
        self.result['rows'][0]['values']['3']='10.01'
        result=self.run_read();self.assertFalse(result['all_correct']);self.assertFalse(result['platform_write'])
        self.assertEqual(result['differences'][0]['actual'],'10.01')

    def test_unknown_wrong_offer_missing_scope_wrong_time_are_not_success(self):
        original=deepcopy(self.result)
        for change in ('offer','scope','time','nan','duplicate'):
            self.result=deepcopy(original);row=self.result['rows'][0]
            if change=='offer':row['window']['offer_id']='9'
            elif change=='scope':row['values']={}
            elif change=='time':row['window']['end']='2028-07-31 23:59:59'
            elif change=='duplicate':self.result['rows']*=2
            else:row['values']['3']='NaN'
            with self.subTest(change=change),self.assertRaises(ValueError):self.run_read()
        self.job['state']='unknown'
        with self.assertRaisesRegex(ValueError,'not_finished'):self.run_read()

    def test_saved_evidence_must_match_and_cannot_escape_roots(self):
        self.run_read();changed=dict(self.result,evidence_path=str(self.path),shop_name='other')
        with self.assertRaises(ValueError):verify(dict(self.job,result=changed),**self.args)
        self.path.write_text('{}',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'observation_changed'):
            verify(dict(self.job,result=dict(self.result,evidence_path=str(self.path))),**self.args)
