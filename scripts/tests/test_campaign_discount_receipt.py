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

    def partial_job(self):
        from openpyxl import Workbook
        from campaign_entry_authority import file_sha
        job=self.job();result=job['result'];values=result['readbacks'][0]['values']
        sku=next(iter(values));values.pop(sku)
        failed=[dict(item=entry.ITEM,sku=sku,message='失败原文')]
        report=self.root/'failed.xlsx';wb=Workbook();ws=wb.active
        ws.append(['商品ID','SKUID','失败原因']);ws.append([entry.ITEM,sku,'失败原文'])
        wb.save(report);wb.close()
        result.update(state='verified_partial_offer_terminal',success=len(values),failed=1,
                      failure_rows=failed,feedback={'path':str(report),'sha256':file_sha(report)})
        result.pop('evidence_path')
        result['evidence_path']=str(self.write('partial-job.json',result))
        return job

    def test_mixed_product_stays_unknown_and_cannot_replay(self):
        job=self.partial_job()
        result=reconcile_discount(self.auth,job,output_dir=self.root/'partial-out')
        self.assertEqual(result['items'],[dict(item=entry.ITEM,outcome='partial')])
        self.assertEqual(result['errors'][0]['message'],'失败原文')
        self.assertEqual(self.auth.db.execute('SELECT status FROM attempts').fetchone()[0],'unknown')
        self.assertEqual(self.auth.blocked(entry.CAMPAIGN,'discount',entry.START,entry.END)[entry.ITEM],'unknown')

    def test_partial_report_file_change_never_becomes_success(self):
        from pathlib import Path
        job=self.partial_job();Path(job['result']['feedback']['path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'hash_changed'):
            reconcile_discount(self.auth,job,output_dir=self.root/'partial-out')

    def test_partial_report_success_readback_must_cover_complement_exactly(self):
        job=self.partial_job();result=job['result'];result['readbacks'][0]['values'].clear()
        result.pop('evidence_path');result['evidence_path']=str(self.write('partial-empty-readback.json',result))
        with self.assertRaisesRegex(ValueError,'sku_amount_mismatch'):
            reconcile_discount(self.auth,job,output_dir=self.root/'partial-out')
