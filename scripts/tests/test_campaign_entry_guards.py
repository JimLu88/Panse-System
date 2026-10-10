"""Isolated synthetic authority; never claims a real campaign or opens a browser."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from campaign_entry_authority import Authority, file_sha, validate_price
from campaign_discount_reuse import reconcile, historical_offer
from campaign_generate_current_files import _generate, build_rows
from campaign_price_snapshot import build_snapshot, digest
from campaign_official_template import discount_rate
from campaign_submission_gate import run_once, validated_body
from test_campaign_current_rate import rate_fixture

ITEM='917179577721'
SKU='6241018727158'
CAMPAIGN='1/2/3'
START='2026-10-01 00:00:00'
END='2026-10-07 23:59:59'


class EntryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.manifest=self.write('manifest.json',{'sources':[]})
        self.auth=Authority(self.root/'state.sqlite3',self.manifest);self.addCleanup(self.auth.close)
        self.row=dict(item=ITEM,sku=SKU,alt=[],code='NORMAL',daily='100.00',medium_target='75.00',big_target='70.00',custom=False)

    def write(self,name,value):
        p=self.root/name;p.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8');return p

    def register(self,name,kind,document):
        p=self.write(name,document);self.auth.register_source(p,kind,file_sha(p));return p

    def generate(self,name='result',row=None,**kwargs):
        base=row or self.row
        rows=[dict(base,sku=str(6241018727157+n),code=base['code']+str(n)) for n in range(4)]
        snapshot=self.write(name+'-snapshot.json',build_snapshot(rows))
        template=self.root/(name+'-template.xlsx');template.write_bytes(rate_fixture('12%'))
        args=SimpleNamespace(output_dir=self.root/name,snapshot=snapshot,activity_template=template,
            start=START,end=END,campaign_key=CAMPAIGN,official_rate='12%',target='big',
            signup_items=None,discount_items=None,custom_basis_receipt=[],custom_corrections=None,**kwargs)
        result=_generate(args,self.auth)
        return args,result

    def terminal(self,claim,phase,items=None):
        attempt=self.auth.db.execute('SELECT * FROM attempts WHERE id LIKE ?',(claim+':%',)).fetchone()
        body=self.auth.get_bundle(attempt['bundle_id'])
        file=next(f for f in body['files'] if Path(f['path']).name==('活动报名.xlsx' if phase=='signup' else '单品立减.xlsx'))
        items=items or [dict(item=ITEM,status='success')]
        doc=dict(schema='campaign_entry_terminal_v1',claim_id=claim,campaign=CAMPAIGN,phase=phase,
                 start=START,end=END,file_sha256=file['sha256'],batch_id='TEST-BATCH',terminal=True,items=items)
        path=self.write(claim+'.json',doc)
        return dict(doc,evidence_path=str(path))

    def test_new_session_mapping_restored_without_copying_receipt_price(self):
        self.register('map.json','mapping',dict(status='verified_partial_mapping_restored',restored=[dict(item=ITEM,sku=SKU,erp_code='NORMAL',daily='1')]))
        row=dict(self.row,sku='111')
        other=Authority(self.root/'state.sqlite3',self.manifest)
        try:
            resolved=other.resolve_snapshot(build_snapshot([row]))
            self.assertIn(SKU,resolved['all_erp_rows'][0]['alt'])
            self.assertEqual(resolved['all_erp_rows'][0]['daily'],'100.00')
        finally:other.close()

    def test_mapping_conflict_not_overwritten(self):
        self.register('map.json','mapping',dict(status='verified_partial_mapping_restored',restored=[dict(item=ITEM,sku=SKU,erp_code='NORMAL')]))
        with self.assertRaisesRegex(ValueError,'conflicts'):
            self.auth.resolve_snapshot(build_snapshot([dict(self.row,sku='111'),dict(self.row,code='OTHER')]))

    def test_rotation_overlay_idempotent_and_partial_scope_safe(self):
        self.register('rotation.json','rotation',dict(official_success=True,batch_id='R',item_ids=[ITEM],new_sku_mapping={'111':SKU,'unrelated':'unrelated-new'}))
        once=self.auth.resolve_snapshot(build_snapshot([dict(self.row,sku='111')]))
        twice=self.auth.resolve_snapshot(once)
        self.assertEqual(once,twice)

    def test_missing_original_does_not_block_unchanged_custom(self):
        self.auth.config['sources']=[dict(kind='fixed',path=str(self.root/'missing.json'),sha256='missing')]
        _,r=self.generate(row=dict(self.row,custom=True))
        self.assertFalse(r['issues']);self.assertEqual(r['activity_rows'][0]['activity_price'],'100.00')

    def test_custom_floor_inherits_verified_alias(self):
        self.register('fixed.json','fixed',dict(rows=[dict(item=ITEM,sku='111',erp_code='NORMAL',fixed_original_record='500.00',fixed_floor='100.00')]))
        basis=self.auth.bases(build_snapshot([dict(self.row,alt=['111'])]))[(ITEM,SKU)]
        self.assertEqual(basis['floor'],'100.00')
        validate_price(dict(custom=True,activity_price='100.00'),'120',basis,lowering_authorized=True,failed_exact=True)
        with self.assertRaisesRegex(ValueError,'twenty_percent'):
            validate_price(dict(custom=True,activity_price='99.99'),'120',basis,lowering_authorized=True,failed_exact=True)

    def test_custom_missing_exact_authority_or_failure_rejected(self):
        basis=dict(original='500',floor='100',source='test')
        for auth,failed in [(False,True),(True,False),(False,False)]:
            with self.subTest(auth=auth,failed=failed),self.assertRaisesRegex(ValueError,'authority_required'):
                validate_price(dict(custom=True,activity_price='100'),'120',basis,lowering_authorized=auth,failed_exact=failed)
        with self.assertRaisesRegex(ValueError,'basis_unknown'):
            validate_price(dict(custom=True,activity_price='100'),'120',None,lowering_authorized=True,failed_exact=True)

    def test_rebased_original_conflict(self):
        for n,original in enumerate([500,400]):
            self.register(str(n)+'.json','fixed',dict(rows=[dict(item=ITEM,sku=SKU,erp_code='NORMAL',fixed_original_record=str(original),fixed_floor=str(original/5))]))
        with self.assertRaisesRegex(ValueError,'no_rebase'):self.auth.bases(build_snapshot([self.row]))

    def test_two_phase_once_and_new_chat_replay_blocked(self):
        _,result=self.generate();identity=result['entry_bundle_id'];calls=[]
        with self.assertRaisesRegex(ValueError,'discount_terminal_success_required'):
            run_once(identity,'signup',lambda payload:calls.append(payload),authority=self.auth)
        def callback(payload):
            calls.append(payload);return self.terminal(payload['claim_id'],payload['phase'])
        run_once(identity,'discount',callback,authority=self.auth)
        run_once(identity,'signup',callback,authority=self.auth)
        self.assertEqual(len(calls),2)
        other=Authority(self.root/'state.sqlite3',self.manifest)
        try:
            with self.assertRaisesRegex(ValueError,'must_not_replay'):
                run_once(identity,'signup',callback,authority=other)
        finally:other.close()
        self.assertEqual(len(calls),2)

    def test_callback_exception_persists_unknown_no_retry(self):
        _,r=self.generate(row=dict(self.row,custom=True));identity=r['entry_bundle_id']
        def fail(_):raise TimeoutError('browser timeout')
        with self.assertRaises(TimeoutError):run_once(identity,'signup',fail,authority=self.auth)
        other=Authority(self.root/'state.sqlite3',self.manifest)
        try:self.assertEqual(other.blocked(CAMPAIGN,'signup',START,END),{ITEM:'unknown'})
        finally:other.close()
        with self.assertRaisesRegex(ValueError,'must_not_replay'):run_once(identity,'signup',fail,authority=self.auth)

    def test_success_protects_whole_item_not_other_campaign(self):
        _,r=self.generate(row=dict(self.row,custom=True));claim=self.auth.claim(r['entry_bundle_id'],'signup')
        self.auth.terminal(claim,self.terminal(claim,'signup'))
        self.assertIn(ITEM,self.auth.blocked(CAMPAIGN,'signup','2027-01-01','2027-01-02'))
        self.assertFalse(self.auth.blocked('9/8/7','signup',START,END))

    def test_atomic_different_bundles_same_item(self):
        _,a=self.generate('a',row=dict(self.row,custom=True));_,b=self.generate('b',row=dict(self.row,custom=True))
        def claim(identity):
            authority=Authority(self.root/'state.sqlite3',self.manifest)
            try:
                authority.claim(identity,'signup');return True
            except ValueError:return False
            finally:authority.close()
        with ThreadPoolExecutor(2) as pool:self.assertEqual(sorted(pool.map(claim,[a['entry_bundle_id'],b['entry_bundle_id']])),[False,True])

    def test_terminal_unbound_proof_leaves_unknown(self):
        _,r=self.generate(row=dict(self.row,custom=True));claim=self.auth.claim(r['entry_bundle_id'],'signup')
        proof=self.terminal(claim,'signup');self.write(Path(proof['evidence_path']).name,dict(unrelated=True))
        with self.assertRaisesRegex(ValueError,'not_bound'):self.auth.terminal(claim,proof)
        self.assertEqual(self.auth.blocked(CAMPAIGN,'signup',START,END)[ITEM],'unknown')

    def test_missing_terminal_keeps_unknown(self):
        _,r=self.generate(row=dict(self.row,custom=True));claim=self.auth.claim(r['entry_bundle_id'],'signup')
        self.auth.terminal(claim,dict(terminal=False))
        self.assertEqual(self.auth.blocked(CAMPAIGN,'signup',START,END)[ITEM],'unknown')

    def test_tampered_upload_never_reaches_callback(self):
        _,r=self.generate();f=next(f for f in r['files'] if Path(f['path']).name=='单品立减.xlsx')
        Path(f['path']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'upload_file_changed'):run_once(r['entry_bundle_id'],'discount',lambda _:self.fail('called'),authority=self.auth)

    def test_tampered_snapshot_and_template_rejected(self):
        for kind in ['snapshot','activity_template']:
            args,r=self.generate(kind)
            getattr(args,kind).write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'changed'):validated_body(self.auth,r['entry_bundle_id'],'signup')

    def test_forged_ordinary_price_or_target_rejected(self):
        _,r=self.generate()
        for field,value in [('activity_price','99'),('big_target','1')]:
            body=self.auth.get_bundle(r['entry_bundle_id']);body['signup_rows'][0][field]=value
            identity=self.auth.save_bundle(body)
            with self.assertRaises(ValueError):validated_body(self.auth,identity,'signup')

    def test_source_mutation_rejected(self):
        path=self.register('map.json','mapping',dict(status='verified_partial_mapping_restored',restored=[]))
        _,r=self.generate();path.write_text('{}')
        with self.assertRaisesRegex(ValueError,'source_version'):validated_body(self.auth,r['entry_bundle_id'],'signup')

    def offer(self,deduct='259.52',**kwargs):
        return dict(offer_id='ACTUAL',start=START,end=END,items=[dict(item=ITEM,status='success')],rows=[dict(item=ITEM,sku=SKU,deduct=deduct)],**kwargs)

    def price_rows(self):
        row=dict(self.row,daily='1095.00',big_target='704.08')
        a,d,issues=build_rows(build_snapshot([row]),[dict(item=ITEM,sku=SKU,state='草稿')],Decimal('.12'),'big',{})
        self.assertFalse(issues);return a,d

    def test_actual_deduction_undercut_not_hidden_by_ideal_formula(self):
        a,d=self.price_rows();new,reuse,issues=reconcile(a,d,[self.offer()],START,END,Decimal('.12'))
        self.assertEqual(d[0]['deduct'],'258.92')
        self.assertFalse(reuse);self.assertEqual(issues[0]['final'],'703.48')
        self.assertEqual(issues[0]['delta'],'-0.60');self.assertIn('below_big_floor',issues[0]['error'])

    def test_exact_reuse_never_reuploads(self):
        a,d=self.price_rows();new,reuse,issues=reconcile(a,d,[self.offer('258.92')],START,END,Decimal('.12'))
        self.assertFalse(new);self.assertFalse(issues);self.assertEqual(len(reuse),1)

    def test_higher_than_target_also_not_exact_closed_loop(self):
        a,d=self.price_rows();_,_,issues=reconcile(a,d,[self.offer('258')],START,END,Decimal('.12'))
        self.assertIn('not_frozen_target',issues[0]['error'])

    def test_missing_sku_unknown_window_and_overlap(self):
        a,d=self.price_rows()
        for change,error in [({'rows':[]},'missing'),({'items':[dict(item=ITEM,status='unknown')]},'unknown'),({'start':'2026-09-01 00:00:00'},'window')]:
            offer=dict(self.offer(),**change)
            with self.subTest(error=error):
                _,_,issues=reconcile(a,d,[offer],START,END,Decimal('.12'));self.assertIn(error,issues[0]['error'])
        _,_,issues=reconcile(a,d,[self.offer(),dict(self.offer(),offer_id='OTHER')],START,END,Decimal('.12'))
        self.assertIn('multiple',issues[0]['error'])

    def test_discount_scope_omission_not_zero(self):
        a,d=self.price_rows();_,_,issues=reconcile(a,[],[],START,END,Decimal('.12'))
        self.assertIn('missing_from_selected_scope',issues[0]['error'])

    def test_nonoverlap_discount_does_not_block(self):
        a,d=self.price_rows();offer=dict(self.offer(),start='2025-01-01',end='2025-01-02')
        new,reuse,issues=reconcile(a,d,[offer],START,END,Decimal('.12'))
        self.assertEqual(new,d);self.assertFalse(issues)

    def test_generator_integrates_actual_reuse_error_no_upload_file(self):
        offer=self.offer()
        offer['rows']=[dict(item=ITEM,sku=str(6241018727157+n),deduct='259.52') for n in range(4)]
        with patch.object(self.auth,'discount_offers',return_value=[offer]):
            _,r=self.generate(row=dict(self.row,daily='1095.00',big_target='704.08'))
        self.assertEqual(len(r['issues']),4)
        self.assertEqual({i['error'] for i in r['issues']},{'actual_reused_discount_final_below_big_floor'})
        self.assertFalse(r['files']);self.assertNotIn('entry_bundle_id',r)

    def test_generator_reuses_exact_offer_and_submits_only_signup(self):
        offer=self.offer('18.00')
        offer['rows']=[dict(item=ITEM,sku=str(6241018727157+n),deduct='18.00') for n in range(4)]
        with patch.object(self.auth,'discount_offers',return_value=[offer]):
            _,r=self.generate()
            self.assertFalse(r['discount_rows']);self.assertEqual(len(r['discount_reuse']),4)
            self.assertEqual(len(r['files']),1)
            def callback(payload):return self.terminal(payload['claim_id'],'signup')
            result=run_once(r['entry_bundle_id'],'signup',callback,authority=self.auth)
            self.assertEqual(result['result']['items'][0]['status'],'success')

    def test_newly_unknown_discount_blocks_existing_ready_bundle(self):
        _,r=self.generate()
        offer=dict(self.offer(),items=[dict(item=ITEM,status='unknown')])
        with patch.object(self.auth,'discount_offers',return_value=[offer]):
            with self.assertRaisesRegex(ValueError,'outcome_unknown'):
                run_once(r['entry_bundle_id'],'discount',lambda _:self.fail('called'),authority=self.auth)

    def test_exact_failed_custom_correction_generation(self):
        records=[dict(item=ITEM,sku=str(6241018727157+n),erp_code='NORMAL'+str(n),fixed_original_record='500',fixed_floor='100') for n in range(4)]
        self.register('fixed.json','fixed',dict(rows=records))
        prices=[dict(item=ITEM,sku=SKU,activity_price='100')]
        auth=self.write('auth.json',dict(campaign=CAMPAIGN,authorized_custom_prices=prices))
        failed=self.write('failure.json',dict(campaign=CAMPAIGN,terminal=True,batch_id='TEST-FAIL',rows=[dict(item=ITEM,sku=SKU,status='failed')]))
        correction=dict(prices[0],authorization_path=str(auth),authorization_sha256=file_sha(auth),failure_path=str(failed),failure_sha256=file_sha(failed))
        path=self.write('corrections.json',dict(rows=[correction]))
        args,first=self.generate('initial',row=dict(self.row,custom=True,daily='120'))
        args.output_dir=self.root/'corrected';args.custom_corrections=path
        result=_generate(args,self.auth)
        self.assertFalse(result['issues'])
        self.assertEqual(next(r for r in result['activity_rows'] if r['sku']==SKU)['activity_price'],'100')
        validated_body(self.auth,result['entry_bundle_id'],'signup')

    def test_whole_frozen_contract_fingerprint_enforced(self):
        import campaign_entry_authority as module
        original=module.load
        def changed(path):
            result=original(path)
            if Path(path).name=='campaign-signup-frozen-contract.json':result['price_targets']['super88_12_percent']='medium_promo'
            return result
        with patch.object(module,'load',side_effect=changed),self.assertRaisesRegex(ValueError,'contract_changed'):
            Authority(self.root/'changed.sqlite3',self.manifest)


if __name__=='__main__':unittest.main()
