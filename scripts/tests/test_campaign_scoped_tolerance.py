"""Current scoped authorization exercised through generation and claim; no network."""
from decimal import Decimal
from pathlib import Path
import unittest
from unittest.mock import patch

import test_campaign_entry_guards as entry
from campaign_discount_reuse import reconcile
from campaign_entry_authority import validate_price
from campaign_generate_current_files import _generate
from campaign_scoped_tolerance import SCOPE, policy_for
from campaign_submission_gate import validated_body, run_once


class ScopedToleranceTests(unittest.TestCase):
    setUp = entry.EntryTests.setUp
    write = entry.EntryTests.write
    generate = entry.EntryTests.generate
    offer = entry.EntryTests.offer
    price_rows = entry.EntryTests.price_rows
    terminal = entry.EntryTests.terminal

    def scope(self):
        return dict(campaign=SCOPE[0],target='big')

    def setup_campaign(self):
        for key,value in [('CAMPAIGN',SCOPE[0]),('START',SCOPE[1]),('END',SCOPE[2])]:
            self.enterContext(patch.object(entry,key,value))

    def test_boundaries_both_directions(self):
        a,d=self.price_rows()
        for delta in ['0','2.00','-2.00','2.01','-2.01']:
            with self.subTest(delta=delta):
                offer=dict(self.offer(str(Decimal('258.92')-Decimal(delta))),start=SCOPE[1],end=SCOPE[2])
                new,reuse,issues=reconcile(a,d,[offer],SCOPE[1],SCOPE[2],SCOPE[3],**self.scope())
                allowed=abs(Decimal(delta))<=2
                self.assertEqual(not issues,allowed)
                self.assertEqual(len(reuse),int(allowed))
                if allowed:
                    self.assertFalse(new)
                    self.assertEqual(Decimal(reuse[0]['delta']),Decimal(delta))
                    self.assertEqual(reuse[0]['final_price_tolerance']['max_absolute_delta_cny'],'2.00')

    def test_new_rule_opt_in_does_not_reinterpret_old_bundle(self):
        from campaign_continuous_policy import RULE_SHA
        self.assertIsNone(policy_for(entry.CAMPAIGN,entry.START,entry.END,Decimal('.12'),'big'))
        policy=policy_for(entry.CAMPAIGN,entry.START,entry.END,Decimal('.12'),'big',continuous_rule_sha=RULE_SHA)
        self.assertEqual(policy['authorization_sha256'],RULE_SHA)
        self.assertEqual(policy['max_absolute_delta_cny'],'2.00')
        for sha,target in [('fake','big'),(RULE_SHA,'medium')]:
            with self.assertRaises(ValueError):
                policy_for(entry.CAMPAIGN,entry.START,entry.END,Decimal('.12'),target,continuous_rule_sha=sha)
        _,result=self.generate(row=dict(self.row,custom=True),continuous_rule_sha=RULE_SHA)
        body=self.auth.get_bundle(result['entry_bundle_id'])
        self.assertEqual(body['continuous_rule_sha'],RULE_SHA)
        validated_body(self.auth,result['entry_bundle_id'],'signup')
        body.pop('continuous_rule_sha')
        changed=self.auth.save_bundle(body)
        with self.assertRaisesRegex(ValueError,'tolerance_version_changed'):
            validated_body(self.auth,changed,'signup')

    def test_scope_isolation_campaign_window_rate_target(self):
        scope=list(SCOPE)
        self.assertIsNotNone(policy_for(*scope))
        for index,value in [(0,'9/8/7'),(1,'2026-09-16 00:00:00'),(2,'2026-09-28 23:59:59'),(3,Decimal('.15')),(4,'medium')]:
            candidate=scope.copy();candidate[index]=value
            with self.subTest(index=index):self.assertIsNone(policy_for(*candidate))
        a,d=self.price_rows()
        offer=dict(self.offer(),start=SCOPE[1],end=SCOPE[2])
        _,_,issues=reconcile(a,d,[offer],SCOPE[1],SCOPE[2],SCOPE[3],campaign='9/8/7',target='big')
        self.assertIn('below_big_floor',issues[0]['error'])

    def test_invalid_amount_overlap_unknown_and_window_still_block(self):
        a,d=self.price_rows()
        good=dict(self.offer(),start=SCOPE[1],end=SCOPE[2])
        variants=[dict(good,rows=[dict(item=entry.ITEM,sku=entry.SKU,deduct=v)]) for v in ['NaN','Infinity','-1','0','1.001']]
        variants += [dict(good,rows=[]),dict(good,items=[dict(item=entry.ITEM,status='unknown')]),dict(good,start='2026-09-15 20:00:00')]
        for offer in variants:
            _,reuse,issues=reconcile(a,d,[offer],SCOPE[1],SCOPE[2],SCOPE[3],**self.scope())
            self.assertTrue(issues);self.assertFalse(reuse)
        _,_,issues=reconcile(a,d,[good,dict(good,offer_id='SECOND')],SCOPE[1],SCOPE[2],SCOPE[3],**self.scope())
        self.assertIn('overlapping',issues[0]['error'])

    def test_nonpositive_final_is_not_tolerance(self):
        a=[dict(item=entry.ITEM,sku=entry.SKU,activity_price='3.00',custom=False,target='1.00',big_target='1.00')]
        offer=dict(self.offer('2.64'),start=SCOPE[1],end=SCOPE[2])
        _,reuse,issues=reconcile(a,[],[offer],SCOPE[1],SCOPE[2],SCOPE[3],**self.scope())
        self.assertFalse(reuse);self.assertIn('nonpositive',issues[0]['error'])

    def test_custom_floor_and_ordinary_daily_not_relaxed(self):
        with self.assertRaisesRegex(ValueError,'twenty_percent'):
            validate_price(dict(custom=True,activity_price='198'),'1000',dict(original='1000',floor='200',source='user'),lowering_authorized=True,failed_exact=True)
        with self.assertRaisesRegex(ValueError,'ordinary_signup'):
            validate_price(dict(custom=False,activity_price='98'),'100',None)
        a,d=self.price_rows();a[0]['custom']=True
        offer=dict(self.offer(),start=SCOPE[1],end=SCOPE[2])
        _,_,issues=reconcile(a,d,[offer],SCOPE[1],SCOPE[2],SCOPE[3],**self.scope())
        self.assertIn('custom_sku',issues[0]['error'])

    def test_generation_claim_and_success_protection_consume_same_policy(self):
        self.setup_campaign()
        offer=self.offer()
        offer['rows']=[dict(item=entry.ITEM,sku=str(6241018727157+n),deduct='259.52') for n in range(4)]
        with patch.object(self.auth,'discount_offers',return_value=[offer]):
            _,result=self.generate(row=dict(self.row,daily='1095.00',big_target='704.08'))
            self.assertFalse(result['issues']);self.assertFalse(result['discount_rows'])
            self.assertEqual(len(result['discount_reuse']),4)
            self.assertTrue(all(r['activity_price']=='1095.00' for r in result['activity_rows']))
            identity=result['entry_bundle_id']
            validated_body(self.auth,identity,'signup')
            calls=[]
            def callback(payload):
                calls.append(payload);return self.terminal(payload['claim_id'],'signup')
            run_once(identity,'signup',callback,authority=self.auth)
            with self.assertRaisesRegex(ValueError,'must_not_replay'):
                run_once(identity,'signup',callback,authority=self.auth)
            self.assertEqual(len(calls),1)

    def test_policy_tamper_or_missing_rejected_by_both_entry_paths(self):
        self.setup_campaign()
        _,result=self.generate(row=dict(self.row,custom=True))
        for value in [None,dict(result['final_price_tolerance'],max_absolute_delta_cny='3.00')]:
            body=self.auth.get_bundle(result['entry_bundle_id']);body['final_price_tolerance']=value
            identity=self.auth.save_bundle(body)
            for action in [lambda:validated_body(self.auth,identity,'signup'),lambda:self.auth.claim(identity,'signup')]:
                with self.assertRaisesRegex(ValueError,'tolerance_version_changed'):action()
        self.assertEqual(self.auth.db.execute('SELECT count(*) FROM attempts').fetchone()[0],0)

    def test_invalid_source_fails_before_claim(self):
        self.setup_campaign()
        _,result=self.generate(row=dict(self.row,custom=True))
        import campaign_scoped_tolerance as module
        fake=self.write('bad-authorization.json',{})
        with patch.object(module,'RECEIPT',fake):
            with self.assertRaisesRegex(ValueError,'authorization_changed'):
                self.auth.claim(result['entry_bundle_id'],'signup')

    def test_new_unknown_and_more_than_two_still_rejected_at_claim(self):
        self.setup_campaign()
        offer=self.offer()
        offer['rows']=[dict(item=entry.ITEM,sku=str(6241018727157+n),deduct='259.52') for n in range(4)]
        with patch.object(self.auth,'discount_offers',return_value=[offer]):
            _,result=self.generate(row=dict(self.row,daily='1095.00',big_target='704.08'))
        for changed in [dict(offer,items=[dict(item=entry.ITEM,status='unknown')]),dict(offer,rows=[dict(r,deduct='260.93') for r in offer['rows']])]:
            with patch.object(self.auth,'discount_offers',return_value=[changed]):
                with self.assertRaises(ValueError):self.auth.claim(result['entry_bundle_id'],'signup')
        self.assertEqual(self.auth.db.execute('SELECT count(*) FROM attempts').fetchone()[0],0)


if __name__=='__main__':unittest.main()
