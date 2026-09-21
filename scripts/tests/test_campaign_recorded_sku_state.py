import unittest
from copy import deepcopy
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from campaign_recorded_sku_state import disabled_from_rows, disabled_from_batch


class RecordedSwitchTests(unittest.TestCase):
    def fixture(self):
        fact=dict(item='1',sku='11',attributes='床板材质:松木;颜色分类:床1.2米;',sku_code='',price='7580',stock='0')
        row=dict(index=0,enabled=False,switches=[dict(aria='false',class_name='next-switch next-switch-off')],
                 cells=[dict(text='松木'),dict(text='床1.2米'),dict(text='元',fields=[dict(value='7580.00')]),
                        dict(text='件',fields=[dict(value='0')])])
        scope=dict(complete=True,sku_facts=[dict(facts=fact)])
        record=dict(item='1',requested_skus=['11'],state='read',platform_write=False,rows=[row])
        return scope,record

    def test_exact_disabled(self):
        s,r=self.fixture();self.assertEqual(disabled_from_rows(s,[r]),[dict(item='1',sku='11',row_index=0)])

    def test_zero_stock_and_absence_never_disable(self):
        for state in (True,None):
            s,r=self.fixture();r['rows'][0]['enabled']=state;self.assertEqual(disabled_from_rows(s,[r]),[])
        s,r=self.fixture();r['rows']=[];self.assertEqual(disabled_from_rows(s,[r]),[])

    def test_spec_price_code_and_duplicates_protected(self):
        for kind in ('material','price','code','duplicate_export','duplicate_dom','wrong_item','wrong_sku','switch_conflict'):
            with self.subTest(kind=kind):
                s,r=self.fixture();row=r['rows'][0]
                if kind=='material':row['cells'][0]['text']='榉木'
                if kind=='price':row['cells'][2]['fields'][0]['value']='8500'
                if kind=='code':row['cells'].append(dict(text='',fields=[dict(value='PPS12345678901')]))
                if kind=='duplicate_export':s['sku_facts'].append(deepcopy(s['sku_facts'][0]))
                if kind=='duplicate_dom':r['rows'].append(deepcopy(row))
                if kind=='wrong_item':r['item']='2'
                if kind=='wrong_sku':r['requested_skus']=['22']
                if kind=='switch_conflict':row['switches'][0]['aria']='true'
                self.assertEqual(disabled_from_rows(s,[r]),[])

    def test_partial_batch_keeps_only_exact_positive_off_facts(self):
        s,r=self.fixture()
        failed=dict(item='2',requested_skus=['22'],state='blocked',reason='TimeoutError',platform_write=False)
        for state,error in [('batch_read_complete',None),('batch_read_partial','batch_evidence_incomplete')]:
            batch=dict(state=state,error=error,platform_write=False,records=[r,failed],unread_items=['1','2'],
                       recording=dict(capture_errors=1))
            self.assertEqual(disabled_from_batch(s,batch),[dict(item='1',sku='11',row_index=0)])
            self.assertEqual(batch['unread_items'],['1','2'])
            self.assertEqual(batch['recording']['capture_errors'],1)

    def test_duplicate_item_even_conflicting_blocked_is_not_chosen(self):
        s,r=self.fixture()
        other=dict(item='1',state='blocked',platform_write=False)
        self.assertEqual(disabled_from_rows(s,[r,other]),[])

    def test_top_level_failure_or_write_is_rejected(self):
        s,r=self.fixture()
        for extra in [dict(state='batch_read_blocked'),dict(error='human_login_or_security_gate'),dict(platform_write=True)]:
            batch=dict(state='batch_read_partial',error='batch_evidence_incomplete',platform_write=False,records=[r])
            batch.update(extra)
            with self.assertRaisesRegex(ValueError,'recorded_state_batch_incomplete'):disabled_from_batch(s,batch)


if __name__=='__main__':unittest.main()
