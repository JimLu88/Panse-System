from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from campaign_product_scope import parse_export, complete_scope, unique_mappings
import hashlib


class ScopeTests(unittest.TestCase):
    def test_duplicate_merchant_headers_resolve_sku_block(self):
        rows={3:{'A':'商品Id','G':'商家编码','J':'销售属性','L':'skuId','M':'价格(元)','N':'库存(件)','P':'商家编码'},
              4:{'A':'1','G':'PRODUCT','J':'黑色','L':'11','M':'100','N':'0','P':'SKU'},
              5:{'L':'12','M':'200','N':'100','P':'SECOND'}}
        with patch('campaign_product_scope.read_rows',return_value=rows):
            result=parse_export(b'not-used')
        self.assertEqual(result[0]['sku_code'],'SKU')
        self.assertEqual(result[1]['item'],'1')
        self.assertEqual(result[0]['stock'],'0')
        self.assertNotIn('on_sale',result[0])

    def scope(self, rows, ids=('1',)):
        raw=b'test-only'
        with patch('campaign_product_scope.parse_export',return_value=rows):
            return complete_scope([(raw,hashlib.sha256(raw).hexdigest())],observed_item_ids=list(ids),
                observed_page_count=1,observed_total=len(ids),page_evidence='current-on-sale-page')

    def test_partial_file_does_not_prove_all_selected(self):
        with self.assertRaisesRegex(ValueError,'does_not_cover'):
            self.scope([dict(item='1',sku='11',sku_code='SKU',sheet='发布模板',row=4)],('1','2'))

    def test_mapping_requires_unique_exact_code_and_item(self):
        scope=self.scope([dict(item='1',sku='11',sku_code='SKU',sheet='发布模板',row=4)])
        result=unique_mappings(scope,[dict(code='SKU',item='1')])
        self.assertEqual(result['matches'][0]['sku'],'11')
        for rows in ([dict(code='SKUB1',item='1')],[dict(code='SKU',item='2')],
                     [dict(code='SKU',item='1'),dict(code='SKU',item='1')]):
            self.assertFalse(unique_mappings(scope,rows)['matches'])

    def test_zero_quantity_never_delists_a_product(self):
        scope=self.scope([dict(item='1',sku='11',sku_code='SKU',stock='0',sheet='发布模板',row=4)])
        self.assertEqual(scope['platform_rows'],[{'item':'1','on_sale':True}])
        self.assertFalse(scope['sku_enabled_state_inferred_from_stock'])
