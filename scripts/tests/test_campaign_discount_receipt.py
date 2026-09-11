import unittest
import test_campaign_entry_guards as entry
from campaign_submission_gate import consume_claim
from campaign_discount_receipt import reconcile_discount


class DiscountReceiptTests(unittest.TestCase):
    setUp=entry.EntryTests.setUp
    write=entry.EntryTests.write
    generate=entry.EntryTests.generate

    def job(self):
        _,generated=self.generate()
        body=self.auth.get_bundle(generated['entry_bundle_id'])
        cid=self.auth.claim(generated['entry_bundle_id'],'discount',transport='dedicated_edge_v1')
        claim=consume_claim(self.auth,cid,'e'*64)
        values={r['sku']:r['deduct'] for r in body['discount_rows']}
        result=dict(state='verified_offer_terminal',success=len(values),failed=0,offer_id='12345678901',
            start=body['start'],end=body['end'],offer_window_readback_required=False,claim=claim,
            readbacks=[dict(item=entry.ITEM,values=values,window=dict(offer_id='12345678901',start=body['start'],end=body['end']))])
        path=self.write('discount-job.json',result)
        return dict(operation='discount',state='finished',job_id='e'*64,result=dict(result,evidence_path=str(path)))

    def test_verified_offer_records_once_and_blocks_replay(self):
        job=self.job()
        for _ in range(2):
            result=reconcile_discount(self.auth,job,output_dir=self.root/'out')
            self.assertEqual(result['items'],[dict(item=entry.ITEM,outcome='success')])
        self.assertEqual(self.auth.db.execute('SELECT status FROM attempts').fetchone()[0],'success')

    def test_aggregate_without_actual_readback_is_not_success(self):
        job=self.job();job['result']['readbacks']=[]
        with self.assertRaises(ValueError):reconcile_discount(self.auth,job,output_dir=self.root/'out')
        self.assertEqual(self.auth.db.execute('SELECT status FROM attempts').fetchone()[0],'unknown')

    def test_changed_job_does_not_consume_another_claim(self):
        job=self.job();job['job_id']='f'*64
        with self.assertRaisesRegex(ValueError,'not_bound'):
            reconcile_discount(self.auth,job,output_dir=self.root/'out')
