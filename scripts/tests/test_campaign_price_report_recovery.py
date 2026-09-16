"""No-discount custom bundles are valid; ordinary evidence remains mandatory."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from campaign_price_report_recovery import reclassify


class ReclassificationTests(unittest.TestCase):
    def test_product_level_shipping_reclassification_keeps_exact_sku_scope(self):
        import campaign_approved_shipping as shipping
        from campaign_continuous_policy import classify
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'terminals').mkdir()
            snapshot=root/'snapshot.json';snapshot.write_text(json.dumps({'all_erp_rows':[]}))
            errors=[dict(item=shipping.ITEM,sku='',kind='unknown',batch=shipping.BATCH,
                terminal='failed',message='该商品需要包邮',official_evidence={'sha256':'original'},
                parse_issue='unparsed_or_incomplete_official_failure')]
            (root/('terminals/signup-'+shipping.BATCH+'.json')).write_text(json.dumps({'errors':errors}))
            body=dict(snapshot_path=str(snapshot),target='medium',official_rate='.1',
                signup_rows=[dict(item=shipping.ITEM,sku=s) for s in shipping.SKUS])
            auth=SimpleNamespace(get_bundle=lambda _:body,bases=lambda _: {})
            result=reclassify(SimpleNamespace(root=root,authority=auth),
                dict(batch=shipping.BATCH,bundle_id='bundle',errors=errors),{},root/'out')
            self.assertEqual({e['sku'] for e in result['errors']},shipping.SKUS)
            self.assertTrue(all(classify(e)['repair']['kind']=='file_shipping' for e in result['errors']))
            self.assertEqual(json.loads((root/('terminals/signup-'+shipping.BATCH+'.json')).read_text())['errors'],errors)

    def test_missing_discount_readback_does_not_block_fixed_custom_basis(self):
        for custom in (True,False):
            with self.subTest(custom=custom), tempfile.TemporaryDirectory() as directory:
                root=Path(directory);(root/'terminals').mkdir()
                erp=[dict(code='C',custom=custom,daily='1000',big_target='700')]
                snapshot=root/'snapshot.json';snapshot.write_text(json.dumps(dict(all_erp_rows=erp)))
                terminal=dict(errors=[dict(item='11',sku='22',kind='list_price',submitted_price='1000',official_cap='750')])
                (root/'terminals/signup-123.json').write_text(json.dumps(terminal))
                body=dict(snapshot_path=str(snapshot),target='big',official_rate='.12',
                          signup_rows=[dict(item='11',sku='22',erp_code='C',activity_price='1000')])
                auth=SimpleNamespace(get_bundle=lambda _:body,
                    bases=lambda _: {('11','22'):dict(original='1000',source_sha256='verified-original')})
                transport=SimpleNamespace(root=root,authority=auth)
                report=dict(batch='123',bundle_id='bundle',errors=terminal['errors'])
                result=reclassify(transport,report,{},root/'out')['errors'][0]
                if custom:
                    self.assertEqual(result['kind'],'custom_price')
                    self.assertEqual(result['feasible_signup_price'],'750')
                    self.assertEqual(result['fixed_original'],'1000')
                else:
                    self.assertEqual(result['kind'],'unknown')
                    self.assertEqual(result['parse_issue'],'exact_existing_discount_readback_missing')
                self.assertFalse((root/'verified-discounts').exists())
