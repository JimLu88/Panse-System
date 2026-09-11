"""Synthetic calendar and isolated authority tests; no browser/store writes."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from campaign_segmented_time import (RULE_SHA, bind_request, build_plan, instant,
                                    load_rules, phase_window, validate_binding)
from campaign_continuous_policy import RULE_SHA as CONTINUOUS_SHA
from campaign_entry_authority import Authority
from campaign_submission_gate import validated_body, verify_claim
import test_campaign_entry_guards as entry_tests
from test_campaign_entry_guards import START, END, CAMPAIGN, ITEM


def request():
    return dict(rule_sha=RULE_SHA, timezone='Asia/Shanghai', shop_id='test-shop',
        price_version='test-price-version', known_campaigns_complete=True,
        discovery_evidence='synthetic-discovery',
        coverage={'start':'2026-09-11 00:00:00','end':'2026-10-02 23:59:59'},
        daily_activity=dict(shop_id='test-shop',campaign='10/20/30',
            start='2026-09-01 00:00:00',end='2028-07-31 23:59:59',
            official_rate='.10',page_evidence='synthetic-daily-page'),
        campaigns=[dict(shop_id='test-shop',campaign='11/21/31',
            start='2026-09-16 20:00:00',end='2026-09-27 23:59:59',
            official_rate='.12',page_evidence='synthetic-big-page')])


class TimeTests(unittest.TestCase):
    def test_rule_version_pinned_and_legacy_policy_unchanged(self):
        self.assertFalse(load_rules()['platform_validity_is_price_window'])
        from campaign_continuous_policy import load_rules as old
        self.assertEqual(old()['rule_id'],'user-continuous-web-agent-20260911')

    def test_daily_big_daily_without_one_second_overlap(self):
        segments = build_plan(request())['segments']
        self.assertEqual([s['target'] for s in segments],['medium','big','medium'])
        self.assertEqual(segments[0]['price_window']['end'],'2026-09-16 19:59:59')
        self.assertEqual(segments[2]['price_window']['start'],'2026-09-28 00:00:00')
        self.assertEqual(segments[2]['price_window']['end'],'2026-10-02 23:59:59')
        self.assertEqual(segments[0]['official_window']['end'],'2028-07-31 23:59:59')
        for a,b in zip(segments,segments[1:]):
            self.assertEqual(instant(a['price_window']['end'])+timedelta(seconds=1),
                             instant(b['price_window']['start']))

    def test_adjacent_big_campaigns_keep_distinct_rates_no_daily_gap(self):
        doc=request()
        doc['campaigns'].append(dict(doc['campaigns'][0],campaign='12/22/32',official_rate='.15',
            start='2026-09-28 00:00:00',end='2026-10-02 23:59:59'))
        segments=build_plan(doc)['segments']
        self.assertEqual([s['target'] for s in segments],['medium','big','big'])
        self.assertEqual([s['official_rate'] for s in segments],['0.10','0.12','0.15'])

    def test_duplicate_exact_activity_is_idempotent_and_order_independent(self):
        doc=request(); expected=build_plan(doc)['segments']
        doc['campaigns']*=2
        self.assertEqual(build_plan(doc)['segments'],expected)

    def test_overlap_even_one_second_and_same_rate_is_not_silently_merged(self):
        for rate in ('.12','.15'):
            doc=request()
            doc['campaigns'].append(dict(doc['campaigns'][0],campaign='12/22/32',official_rate=rate,
                start='2026-09-27 23:59:59',end='2026-10-02 23:59:59'))
            with self.subTest(rate=rate),self.assertRaisesRegex(ValueError,'overlapping_campaigns'):
                build_plan(doc)

    def test_same_campaign_conflicting_evidence_window_rejected(self):
        doc=request();doc['campaigns'].append(dict(doc['campaigns'][0],end='2026-09-28 00:00:00'))
        with self.assertRaisesRegex(ValueError,'conflicting_official'):build_plan(doc)

    def test_year_boundary_and_one_second_daily_gap(self):
        doc=request();doc['coverage']={'start':'2026-12-31 23:59:58','end':'2027-01-01 00:00:01'}
        doc['campaigns'][0].update(start='2026-12-31 23:59:59',end='2027-01-01 00:00:00')
        segments=build_plan(doc)['segments']
        self.assertEqual(len(segments),3)
        self.assertEqual(segments[0]['price_window']['start'],segments[0]['price_window']['end'])
        self.assertEqual(segments[-1]['price_window']['start'],segments[-1]['price_window']['end'])

    def test_leap_day(self):
        doc=request();doc['coverage']={'start':'2028-02-28 23:59:59','end':'2028-03-01 00:00:00'}
        doc['campaigns'][0].update(start='2028-02-29 00:00:00',end='2028-02-29 23:59:59')
        self.assertEqual(len(build_plan(doc)['segments']),3)

    def test_no_known_future_campaign_does_not_extend_to_2028(self):
        doc=request();doc['campaigns']=[]
        segments=build_plan(doc)['segments']
        self.assertEqual(len(segments),1)
        self.assertEqual(segments[0]['price_window'],doc['coverage'])

    def test_campaign_outside_explicit_coverage_not_invented_or_clipped(self):
        doc=request();doc['coverage']['end']='2026-09-15 23:59:59'
        self.assertEqual(len(build_plan(doc)['segments']),1)
        doc['coverage']['end']='2026-09-20 00:00:00'
        with self.assertRaisesRegex(ValueError,'coverage_cuts_campaign'):build_plan(doc)

    def test_coverage_must_be_within_platform_validity(self):
        doc=request();doc['coverage']['end']='2028-08-01 00:00:00'
        with self.assertRaisesRegex(ValueError,'outside_daily'):build_plan(doc)

    def test_unknown_shop_rate_evidence_or_precision_not_assumed(self):
        changes=[('shop_id','wrong'),('page_evidence',''),('official_rate','NaN'),
                 ('official_rate','.20'),('start','2026-09-16'),
                 ('start','2026-09-16T20:00:00+08:00'),('start','2026-09-29 00:00:00')]
        for key,value in changes:
            doc=request();doc['campaigns'][0][key]=value
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):build_plan(doc)
        for key,value in [('rule_sha','old'),('known_campaigns_complete',False),
                          ('timezone','UTC'),('price_version',''),('discovery_evidence','')]:
            doc=request();doc[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):build_plan(doc)

    def test_contract_file_change_rejected(self):
        with patch('campaign_segmented_time.fingerprint',return_value='changed'):
            with self.assertRaisesRegex(ValueError,'require_user'):load_rules()

    def test_legacy_phase_window_has_no_new_source_requirement(self):
        self.assertEqual(phase_window({'start':START,'end':END},'signup'),{'start':START,'end':END})


# Reuse fixtures/helpers, not the base test class (avoid running its full suite twice).
class TimeEntryTests(unittest.TestCase):
    setUp=entry_tests.EntryTests.setUp
    write=entry_tests.EntryTests.write
    generate=entry_tests.EntryTests.generate

    def prepare(self, name='timed'):
        from campaign_generate_current_files import _generate
        # Build local base inputs only; no claim or store/browser mutation.
        args,result=self.generate(name+'-input',continuous_rule_sha=CONTINUOUS_SHA)
        doc=request();doc['campaigns']=[];doc['coverage']={'start':START,'end':END}
        doc['daily_activity'].update(campaign=CAMPAIGN,official_rate='.12')
        # Test big campaign also permits exact phase-specific boundaries; for
        # daily use the actual daily rate .10 in a newly generated official fixture.
        from test_campaign_current_rate import rate_fixture
        args.activity_template.write_bytes(rate_fixture('10%'))
        args.official_rate='10%';args.target='medium'
        doc['daily_activity']['official_rate']='.10'
        doc['price_version']=result['price_version']
        args.time_request=self.write(name+'-time.json',doc)
        args.time_segment=build_plan(doc)['segments'][0]['segment_id']
        args.output_dir=self.root/name
        return args,_generate(args,self.auth)

    def proof(self, claim, phase):
        attempt=self.auth.db.execute('SELECT * FROM attempts WHERE id LIKE ?',(claim+':%',)).fetchone()
        body=self.auth.get_bundle(attempt['bundle_id'])
        file=next(f for f in body['files'] if Path(f['path']).name==
                  ('活动报名.xlsx' if phase=='signup' else '单品立减.xlsx'))
        doc=dict(schema='campaign_entry_terminal_v1',claim_id=claim,campaign=CAMPAIGN,phase=phase,
            start=attempt['start'],end=attempt['end'],file_sha256=file['sha256'],batch_id='TEST',
            terminal=True,items=[dict(item=ITEM,status='success')])
        return dict(doc,evidence_path=str(self.write(claim+'.json',doc)))

    def test_generator_claim_and_terminal_separate_two_time_meanings(self):
        _,r=self.prepare();self.assertFalse(r['issues']);bid=r['entry_bundle_id']
        body,_=validated_body(self.auth,bid,'discount')
        self.assertEqual(phase_window(body,'discount'),{'start':START,'end':END})
        self.assertEqual(phase_window(body,'signup')['end'],'2028-07-31 23:59:59')
        claim=self.auth.claim(bid,'discount',transport='dedicated_edge_v1')
        self.assertEqual(verify_claim(self.auth,claim)['end'],END)
        self.auth.terminal(claim,self.proof(claim,'discount'))
        signup=self.auth.claim(bid,'signup',transport='dedicated_edge_v1')
        self.assertEqual(verify_claim(self.auth,signup)['end'],'2028-07-31 23:59:59')
        self.auth.terminal(signup,self.proof(signup,'signup'))
        # Later price segment does not authorize re-enrollment of same product.
        with self.assertRaisesRegex(ValueError,'must_not_replay'):self.auth.claim(bid,'signup')

    def test_changed_input_source_rejects_before_claim(self):
        args,r=self.prepare();doc=json.loads(args.time_request.read_text(encoding='utf-8'))
        doc['discovery_evidence']='changed';args.time_request.write_text(json.dumps(doc),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'changed'):self.auth.claim(r['entry_bundle_id'],'discount')
        self.assertEqual(self.auth.db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0],0)

    def test_forged_long_discount_window_or_price_version_fails(self):
        _,r=self.prepare();original=self.auth.get_bundle(r['entry_bundle_id'])
        for changes in [{'end':'2028-07-31 23:59:59'},{'price_version':'other'},
                        {'continuous_rule_sha':None},{'target':'big'},{'official_rate':'15%'}]:
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                validate_binding(dict(original,**changes))

    def test_existing_unknown_overlap_not_resized_or_cancelled(self):
        _,old=self.prepare('old');claim=self.auth.claim(old['entry_bundle_id'],'discount')
        before=list(map(tuple,self.auth.db.execute('SELECT * FROM attempts')))
        _,new=self.prepare('new')
        self.assertTrue(new['issues'])
        self.assertEqual(list(map(tuple,self.auth.db.execute('SELECT * FROM attempts'))),before)
        self.assertEqual(self.auth.blocked(CAMPAIGN,'discount',START,END)[ITEM],'unknown')


class TimeFlowTests(unittest.TestCase):
    def setUp(self):
        from campaign_continuous_flow import Store
        from test_campaign_continuous_flow import PAGE
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.store=Store(self.root/'flow.sqlite');self.addCleanup(self.store.close)
        self.doc=request();self.doc['shop_id']='shop-a';self.doc['price_version']='fixed-v1'
        self.doc['campaigns']=[]
        self.doc['daily_activity'].update(shop_id='shop-a',campaign='123/456/789')
        self.page=dict(PAGE,sign_record_id='789',official_rate='.10',
            start=self.doc['daily_activity']['start'],end=self.doc['daily_activity']['end'])
        self.path=self.root/'time.json';self.path.write_text(json.dumps(self.doc),encoding='utf-8')
        self.binding=bind_request(self.path,build_plan(self.doc)['segments'][0]['segment_id'])

    def driver(self, prior=None, *, wrong_window=False, omit_binding=False):
        from test_campaign_continuous_flow import FakeWebAgent
        class Driver(FakeWebAgent):
            def execute(inner,step,action_id,payload):
                out=super().execute(step,action_id,payload)
                if step=='scope':out['prior_outcomes']=prior or {}
                if step=='generate' and not omit_binding:out['time_binding']=payload['time_binding']
                if step=='verify_discount_window':
                    out.update(self.binding['segment']['official_window' if wrong_window else 'price_window'])
                if step=='signup':out['items']=[dict(item=i,outcome='success') for i in payload['items']]
                return out
        return Driver()

    def run_flow(self,driver):
        from campaign_continuous_flow import run
        return run(self.store,driver,self.page,expected_shop='shop-a',
                   observed_links=[self.page['url']],time_binding=self.binding)

    def test_prior_super_success_gets_discount_but_never_signup_again(self):
        driver=self.driver({'1':'success','2':'success'})
        result=self.run_flow(driver)
        self.assertTrue(result['all_signed_up'])
        self.assertEqual([s for s,_ in driver.calls if s in ('discount','signup')],['discount'])
        before=len(driver.calls);self.run_flow(driver);self.assertEqual(before,len(driver.calls))

    def test_mixed_enrolled_and_new_scope_never_reuploads_enrolled_item(self):
        driver=self.driver({'1':'success'})
        self.assertTrue(self.run_flow(driver)['all_signed_up'])
        self.assertEqual([p['items'] for s,p in driver.calls if s=='discount'],[['1','2']])
        self.assertEqual([p['items'] for s,p in driver.calls if s=='signup'],[['2']])

    def test_long_platform_window_is_not_valid_discount_readback(self):
        driver=self.driver(wrong_window=True);result=self.run_flow(driver)
        self.assertEqual(result['blocker']['step'],'verify_discount_window')
        self.assertFalse(any(s=='signup' for s,_ in driver.calls))

    def test_adapter_cannot_drop_time_binding_and_silently_use_legacy(self):
        driver=self.driver(omit_binding=True);result=self.run_flow(driver)
        self.assertEqual(result['blocker']['reason'],'segmented_time_binding_missing_or_changed')
        self.assertFalse(any(s in ('discount','signup') for s,_ in driver.calls))

    def test_price_version_changed_before_generation_stops_without_write(self):
        driver=self.driver();original=driver.execute
        def execute(step,action_id,payload):
            out=original(step,action_id,payload)
            if step=='scope':out['price_version']='changed'
            return out
        driver.execute=execute
        self.assertEqual(self.run_flow(driver)['blocker']['reason'],'time_plan_price_version_changed')
        self.assertFalse(any(s in ('discount','signup') for s,_ in driver.calls))

    def test_page_shop_campaign_and_window_must_match_time_binding(self):
        for key,value in [('sign_record_id','other'),('end','2028-07-30 23:59:59'),('official_rate','.15')]:
            prior=self.page[key];self.page[key]=value;driver=self.driver()
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'does_not_match_official_page'):
                self.run_flow(driver)
            self.assertEqual(driver.calls,[]);self.page[key]=prior


if __name__=='__main__':unittest.main()
