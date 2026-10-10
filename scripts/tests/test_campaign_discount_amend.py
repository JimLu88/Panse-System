"""Isolated fake platform evidence. No production ledger/browser/price writes."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import test_campaign_entry_guards as entry
from campaign_entry_authority import Authority, file_sha
import campaign_discount_amend as amend
from campaign_discount_reuse import reconcile
from campaign_scoped_tolerance import SCOPE
from campaign_submission_gate import validated_body, run_once


class AmendTests(unittest.TestCase):
    write=entry.EntryTests.write
    register=entry.EntryTests.register
    generate=entry.EntryTests.generate
    terminal=entry.EntryTests.terminal

    def setUp(self):
        entry.EntryTests.setUp(self)
        for k,v in [('CAMPAIGN',SCOPE[0]),('START',SCOPE[1]),('END',SCOPE[2])]:self.enterContext(patch.object(entry,k,v))
        evidence=self.write('old-platform-proof.json',{'synthetic':True})
        self.offer_rows=[dict(item=entry.ITEM,sku=str(6241018727157+n),deduct='18.00') for n in range(4)]
        self.register('offer.json','outcome',dict(schema='campaign_entry_outcome_v1',campaign=SCOPE[0],phase='discount',
            batch_id='144956016253',start=SCOPE[1],end=SCOPE[2],discount_rows=self.offer_rows,
            items=[dict(item=entry.ITEM,status='success')],evidence_path=str(evidence),evidence_sha256=file_sha(evidence)))
        self.args,result=self.generate()
        self.signup_bundle=result['entry_bundle_id']
        self.failed_claim=self.auth.claim(self.signup_bundle,'signup')
        terminal=self.terminal(self.failed_claim,'signup',[dict(item=entry.ITEM,status='failed')])
        self.auth.terminal(self.failed_claim,terminal)
        self.source=self.write('official-report.json',{'fixture':'failed'})
        self.failure=self.write('normalized-failures.json',dict(schema='campaign_discount_failure_rows_v1',claim_id=self.failed_claim,
            campaign=SCOPE[0],source=dict(path=str(self.source),sha256=file_sha(self.source)),
            rows=[dict(item=entry.ITEM,sku=r['sku'],status='failed',signup_price='100.00',issue_kind='coupon_final_price',platform_error='fixture coupon cap') for r in self.offer_rows]))
        self.grant=dict(campaign=SCOPE[0],start=SCOPE[1],end=SCOPE[2],offer_id='144956016253',
            failed_signup_claim=self.failed_claim,failure_report_sha256=file_sha(self.source),maximum_amount_adjustment_cny='2.00')
        self.enterContext(patch.object(amend,'authorization',return_value=self.grant))
        self.request=dict(schema='campaign_discount_amend_request_v1',authorization_sha256=amend.AUTHORITY_SHA256,
            **{k:self.grant[k] for k in ('campaign','start','end','offer_id','failed_signup_claim')},
            failure_rows=dict(path=str(self.failure),sha256=file_sha(self.failure)),
            rows=[dict(item=entry.ITEM,sku=entry.SKU,old_deduct='18.00',new_deduct='18.10')])
        self.request_path=self.write('request.json',self.request)

    def readback(self,name='before.json',rows=None,**kwargs):
        source=self.write(name+'-platform.json',dict(synthetic=True,name=name))
        doc=dict(schema='campaign_discount_amount_readback_v1',campaign=SCOPE[0],offer_id='144956016253',
            start=SCOPE[1],end=SCOPE[2],observed_at=datetime.now(timezone.utc).isoformat(),
            source=dict(path=str(source),sha256=file_sha(source)),
            rows=rows or [dict(item=entry.ITEM,sku=entry.SKU,deduct='18.00')])
        doc.update(kwargs)
        return self.write(name,doc)

    def claim(self):
        return amend.claim(self.auth,self.request_path,self.readback())['claim_id']

    def finish(self,identity,status='success',amount='18.10',**kwargs):
        path=self.readback('after.json',rows=[dict(item=entry.ITEM,sku=entry.SKU,deduct=amount,status=status)],
            claim_id=identity,terminal=True,operation_reference='fixture-save',**kwargs)
        return path,amend.record(self.auth,identity,path)

    def test_claim_unknown_blocks_signup_and_discount_replay(self):
        identity=self.claim()
        self.assertTrue(identity)
        self.assertEqual(self.auth.discount_offers()[0]['items'][0]['status'],'unknown')
        with self.assertRaises(ValueError):validated_body(self.auth,self.signup_bundle,'signup')
        with self.assertRaises(ValueError):self.claim()
        other=Authority(self.root/'state.sqlite3',self.manifest)
        try:self.assertEqual(other.discount_offers()[0]['items'][0]['status'],'unknown')
        finally:other.close()

    def test_success_overlay_only_exact_row_and_regeneration_consumes_it(self):
        original=deepcopy(self.auth.discount_offers()[0])
        identity=self.claim();path,states=self.finish(identity)
        offer=self.auth.discount_offers()[0]
        self.assertEqual(states[0]['status'],'success')
        for row in offer['rows']:
            if row['sku']==entry.SKU:
                self.assertEqual(row['deduct'],'18.10');self.assertEqual(row['amendment_receipt']['claim_id'],identity)
            else:self.assertEqual(row,next(r for r in original['rows'] if r['sku']==row['sku']))
        self.assertEqual(json.loads((self.root/'offer.json').read_text())['discount_rows'],self.offer_rows)
        with self.assertRaisesRegex(ValueError,'reuse_evidence_changed'):validated_body(self.auth,self.signup_bundle,'signup')
        _,new=self.generate('after-edit')
        self.assertFalse(new['issues']);self.assertFalse(new['discount_rows'])
        changed=next(r for r in new['discount_reuse'] if r['sku']==entry.SKU)
        self.assertEqual(Decimal(changed['final']),Decimal('69.90'))
        validated_body(self.auth,new['entry_bundle_id'],'signup')
        with self.assertRaises(ValueError):self.claim()

    def test_unknown_write_exception_never_calls_again(self):
        calls=[]
        def edit(payload):calls.append(payload);raise TimeoutError('unknown')
        for _ in range(2):
            with self.assertRaises((ValueError,TimeoutError)):
                amend.run_once(self.auth,self.request_path,lambda _:self.readback(),edit,lambda _:self.fail('not reached'))
        self.assertEqual(len(calls),1)

    def test_failed_official_unchanged_amount_preserves_old_no_same_retry(self):
        identity=self.claim();_,states=self.finish(identity,'failed','18.00')
        self.assertEqual(states[0]['status'],'failed')
        self.assertEqual(next(r for r in self.auth.discount_offers()[0]['rows'] if r['sku']==entry.SKU)['deduct'],'18.00')
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):self.claim()

    def test_cas_old_local_and_platform_mismatch(self):
        for row,readvalue in [(dict(self.request['rows'][0],old_deduct='18.01'),'18.00'),(self.request['rows'][0],'18.01')]:
            request=self.write('mismatch.json',dict(self.request,rows=[row]))
            before=self.readback(rows=[dict(item=entry.ITEM,sku=entry.SKU,deduct=readvalue)])
            with self.assertRaisesRegex(ValueError,'cas_failed'):amend.claim(self.auth,request,before)
        self.assertEqual(self.auth.db.execute('SELECT count(*) FROM discount_amendment_batches').fetchone()[0],0)

    def test_authorization_campaign_and_report_binding(self):
        for key,value in [('campaign','9/8/7'),('offer_id','OTHER'),('end','2026-10-01'),('authorization_sha256','bad'),('failed_signup_claim','OTHER')]:
            with self.subTest(key=key),self.assertRaises(ValueError):
                amend.claim(self.auth,self.write('invalid.json',dict(self.request,**{key:value})),self.readback())
        self.source.write_text('changed')
        with self.assertRaises(ValueError):self.claim()

    def test_unknown_signup_and_success_signup_not_amendable(self):
        for status in ['unknown','success']:
            self.auth.db.execute('UPDATE attempts SET status=? WHERE id=?',(status,self.failed_claim+':'+entry.ITEM))
            with self.assertRaisesRegex(ValueError,'protected'):self.claim()

    def test_duplicate_unknown_and_custom_sku_rejected(self):
        for rows in [self.request['rows']*2,[dict(self.request['rows'][0],sku='OTHER')]]:
            with self.assertRaises(ValueError):amend.validate_request(self.auth,dict(self.request,rows=rows))
        body=self.auth.get_bundle(self.signup_bundle);body['signup_rows'][1]['custom']=True
        altered=self.auth.save_bundle(body)
        self.auth.db.execute('UPDATE attempts SET bundle_id=? WHERE id=?',(altered,self.failed_claim+':'+entry.ITEM))
        with self.assertRaisesRegex(ValueError,'ordinary_only'):self.claim()

    def test_small_delta_boundary_and_final_floor(self):
        for value,allowed in [('20.00',True),('16.00',True),('20.01',False),('15.99',False),('NaN',False),('Infinity',False),('0',False),('18.00',False),('18.001',False)]:
            request=dict(self.request,rows=[dict(self.request['rows'][0],new_deduct=value)])
            if allowed:amend.validate_request(self.auth,request)
            else:
                with self.subTest(value=value),self.assertRaises(ValueError):amend.validate_request(self.auth,request)

    def test_stale_before_and_missing_real_proof_rejected(self):
        before=self.readback(observed_at=(datetime.now(timezone.utc)-timedelta(minutes=6)).isoformat())
        with self.assertRaisesRegex(ValueError,'stale'):amend.claim(self.auth,self.request_path,before)
        before=self.readback();doc=json.loads(before.read_text());doc['source']['sha256']='bad';self.write('before.json',doc)
        with self.assertRaises(ValueError):amend.claim(self.auth,self.request_path,before)

    def test_post_claim_time_wrong_amount_scope_and_claim_rejected(self):
        identity=self.claim()
        cases=[dict(observed_at='2020-01-01T00:00:00+00:00'),dict(claim_id='OTHER'),dict(terminal=False),dict(rows=[dict(item=entry.ITEM,sku=entry.SKU,deduct='18.20',status='success')]),dict(rows=[dict(item=entry.ITEM,sku='OTHER',deduct='18.10',status='success')])]
        for case in cases:
            data=dict(claim_id=identity,terminal=True,operation_reference='fixture',rows=[dict(item=entry.ITEM,sku=entry.SKU,deduct='18.10',status='success')]);data.update(case)
            with self.assertRaises(ValueError):amend.record(self.auth,identity,self.readback('invalid-after.json',**data))
        self.assertEqual(self.auth.discount_offers()[0]['items'][0]['status'],'unknown')

    def test_mutated_confirmed_readback_cannot_feed_signup(self):
        identity=self.claim();path,_=self.finish(identity)
        path.write_text('{}')
        with self.assertRaisesRegex(ValueError,'evidence_changed'):self.auth.discount_offers()

    def test_partial_receipt_keeps_remaining_unknown(self):
        second=dict(item=entry.ITEM,sku='6241018727157',old_deduct='18.00',new_deduct='18.20')
        self.request_path=self.write('request.json',dict(self.request,rows=self.request['rows']+[second]))
        before=self.readback(rows=[dict(item=entry.ITEM,sku=r['sku'],deduct=r['old_deduct']) for r in self.request['rows']+[second]])
        identity=amend.claim(self.auth,self.request_path,before)['claim_id']
        _,states=self.finish(identity)
        self.assertEqual(sorted(r['status'] for r in states),['success','unknown'])
        self.assertEqual(self.auth.discount_offers()[0]['items'][0]['status'],'unknown')

    def test_concurrent_claims_only_one_wins(self):
        from concurrent.futures import ThreadPoolExecutor
        import sqlite3
        before=self.readback()
        def attempt(_):
            other=Authority(self.root/'state.sqlite3',self.manifest)
            try:
                amend.claim(other,self.request_path,before);return True
            except (ValueError,sqlite3.IntegrityError):return False
            finally:other.close()
        with ThreadPoolExecutor(2) as pool:self.assertEqual(sorted(pool.map(attempt,[1,2])),[False,True])
        self.assertEqual(self.auth.db.execute('SELECT count(*) FROM discount_amendment_rows').fetchone()[0],1)


if __name__=='__main__':unittest.main()
