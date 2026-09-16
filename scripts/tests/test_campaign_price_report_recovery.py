"""No-discount custom bundles are valid; ordinary evidence remains mandatory."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from campaign_price_report_recovery import reclassify


class ReclassificationTests(unittest.TestCase):
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
