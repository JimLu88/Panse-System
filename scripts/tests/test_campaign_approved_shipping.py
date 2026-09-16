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

    def test_real_product_level_report_expands_only_original_submitted_skus(self):
        from campaign_feedback_normalization import normalize_errors
        rows=[dict(item=mod.ITEM,sku=sku) for sku in sorted(mod.SKUS)]
        rows.append(dict(item='other',sku='outside'))
        for issue in ('free_shipping_commitment_required','unparsed_or_incomplete_official_failure'):
            error=self.error(message='该商品需要包邮');error.update(sku='',parse_issue=issue)
            normalized=normalize_errors({'errors':[error]},submitted_rows=rows,erp_rows=[],
                fixed_bases={},actual_discounts=[],rate='.1')
            self.assertEqual({e['sku'] for e in normalized},mod.SKUS)
            self.assertTrue(all(classify(e)['action']=='repair' for e in normalized))
            self.assertTrue(all('price' not in classify(e)['repair'] for e in normalized))
            self.assertEqual(error['sku'],'')
            with patch.object(mod,'SOURCE',mod.SOURCE.with_name('not-approved.json')):
                self.assertTrue(all(classify(e)['action']=='manual' for e in normalized))


if __name__=='__main__':unittest.main()
