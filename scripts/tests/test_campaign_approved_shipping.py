import unittest
from unittest.mock import patch
import campaign_approved_shipping as mod
from campaign_continuous_policy import classify


class ShippingTests(unittest.TestCase):
    def error(self,**kw):
        return dict(item=mod.ITEM,sku=next(iter(mod.SKUS)),batch=mod.BATCH,terminal='failed',
                    official_evidence={'path':'original'},kind='unknown',
                    parse_issue='free_shipping_commitment_required',**kw)
    def identity(self):return dict(campaign_id='legacy',phase_id='itemApply',sign_record_id='3172207691')
    def test_exact_user_approval_repairs_without_price_change(self):
        e=self.error();d=classify(e)
        self.assertEqual(d['repair']['kind'],'file_shipping')
        self.assertEqual(mod.projection_values({mod.ITEM:[d]},self.identity()),{mod.ITEM:'1'})
        self.assertNotIn('price',d['repair'])
    def test_other_batch_item_or_sku_stays_manual(self):
        for k in ('batch','sku','item'):
            e=self.error();e[k]='999';self.assertEqual(classify(e)['action'],'manual')
    def test_other_activity_cannot_generate_shipping(self):
        d=classify(self.error())
        with self.assertRaises(ValueError):mod.projection_values({mod.ITEM:[d]},dict(campaign_id='49557'))
    def test_missing_approval_stays_manual(self):
        with patch.object(mod,'SOURCE',mod.SOURCE.with_name('not-approved.json')):
            self.assertEqual(classify(self.error())['action'],'manual')
    def test_success_or_unknown_never_authorized(self):
        for terminal in ('success','unknown'):
            e=self.error();e['terminal']=terminal;self.assertEqual(classify(e)['action'],'manual')
    def test_changed_commitment_rejected(self):
        d=classify(self.error());d['repair']['value']='other'
        with self.assertRaises(ValueError):mod.projection_values({mod.ITEM:[d]},self.identity())


if __name__=='__main__':unittest.main()
