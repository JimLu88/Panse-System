"""Synthetic official observations, isolated local authority; no network."""
from pathlib import Path
import unittest
from unittest.mock import patch
import test_campaign_entry_guards as entry
from campaign_edge_receipt import reconcile_signup
from campaign_submission_gate import consume_claim


class ReceiptTests(unittest.TestCase):
    setUp=entry.EntryTests.setUp
    write=entry.EntryTests.write
    generate=entry.EntryTests.generate

    def job(self, success=1):
        _,generated=self.generate(row=dict(self.row,custom=True))
        claim_id=self.auth.claim(generated['entry_bundle_id'],'signup',transport='dedicated_edge_v1')
        claim=consume_claim(self.auth,claim_id,'b'*64)
        record={'历史记录ID':'12345','执行操作':'商品批量导入','数据来源':'活动报名.xlsx',
                '执行状态':'成功','执行结果':'local test only'}
        observation={'state':'terminal','batch':'12345','record':record,
                     'counts':{'total':1,'success':success,'failed':1-success,'pending':0}}
        path=self.write('official-batch.json',observation)
        return {'operation':'signup','state':'finished','job_id':'b'*64,
                'result':dict(observation,claim=claim,evidence_path=str(path))}

    def test_all_success_readback_records_once_without_resubmission(self):
        job=self.job()
        for _ in range(2):
            result=reconcile_signup(self.auth,job,output_dir=self.root/'reconciled')
            self.assertEqual(result['items'],[{'item':entry.ITEM,'outcome':'success'}])
        self.assertEqual(self.auth.db.execute('SELECT status FROM attempts').fetchone()[0],'success')
        with self.assertRaisesRegex(ValueError,'must_not_replay'):
            self.auth.claim(self.auth.db.execute('SELECT bundle_id FROM attempts').fetchone()[0],'signup')

    def test_all_failed_download_gate_does_not_erase_terminal_failure(self):
        job=self.job(success=0)
        job['result']['report_gate']={'reason':'download_failed'}
        result=reconcile_signup(self.auth,job,output_dir=self.root/'reconciled')
        self.assertEqual(result['items'][0]['outcome'],'failed')
        self.assertEqual(result['errors'],[])
        self.assertEqual(result['report_gate']['reason'],'download_failed')

    def test_wrong_job_or_changed_observation_never_passes(self):
        job=self.job()
        job['job_id']='c'*64
        with self.assertRaisesRegex(ValueError,'not_bound'):
            reconcile_signup(self.auth,job,output_dir=self.root/'reconciled')
        job['job_id']='b'*64
        job['result']['batch']='54321'
        with self.assertRaisesRegex(ValueError,'observation_changed'):
            reconcile_signup(self.auth,job,output_dir=self.root/'reconciled')
        self.assertEqual(self.auth.db.execute('SELECT status FROM attempts').fetchone()[0],'unknown')

    def test_processing_failure_is_terminal_not_timeout(self):
        job=self.job()
        result=job['result']
        result.pop('counts');result['state']='terminal_processing_failed'
        result['record']['执行状态']='处理失败';result['record']['执行结果']='导入模板有误'
        result['evidence_path']=str(self.write('failed.json',{k:v for k,v in result.items() if k in ('state','batch','record')}))
        reconciled=reconcile_signup(self.auth,job,output_dir=self.root/'reconciled')
        self.assertEqual(reconciled['items'][0]['outcome'],'failed')
        self.assertEqual(reconciled['errors'][0]['message'],'导入模板有误')
