from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from campaign_product_scope import parse_export, complete_scope, unique_mappings
import hashlib
import json
import tempfile
from copy import deepcopy
from campaign_product_scope import from_edge_job
from campaign_continuous_policy import fingerprint


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


class EdgeScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.request='a'*64;self.shop='synthetic-shop'
        files=[]
        for index in (1,2):
            path=self.root/f'{index}.xlsx';path.write_bytes(str(index).encode())
            files.append(dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                scope=dict(page=index,page_count=2,total=2,item_ids=[str(index)],on_sale=True),
                record=dict(id=str(index+10),rowCount=1)))
        self.result=dict(state='downloaded',files=files,observed_item_ids=['1','2'],observed_total=2,
                         page_count=2,snapshot_request_id=self.request,shop_name=self.shop)
        self.path=self.root/'receipt.json'
        self.job=dict(operation='product_export',state='finished',
            job_id=fingerprint(['product_export',self.shop,self.request]))
        self.parser=patch('campaign_product_scope.parse_export',side_effect=lambda raw:
            [dict(item=raw.decode(),sku=raw.decode()+'1',sku_code='SKU'+raw.decode(),sheet='发布模板',row=4)])
        self.parser.start();self.addCleanup(self.parser.stop)

    def run_readback(self, result=None):
        value=result or self.result
        self.path.write_text(json.dumps(value),encoding='utf-8')
        return from_edge_job(dict(self.job,result=dict(value,evidence_path=str(self.path))),
            expected_request_id=self.request,expected_shop=self.shop,roots=[self.root])

    def test_downloaded_pages_require_file_coverage_then_produce_scope(self):
        scope=self.run_readback()
        self.assertTrue(scope['complete']);self.assertEqual(scope['observed_item_count'],2)
        self.assertEqual(len(scope['sku_facts']),2)
        self.assertEqual(scope['page_evidence']['job_id'],self.job['job_id'])

    def test_no_partial_page_or_duplicate_record_acceptance(self):
        for change in ('missing','duplicate','wrong_page','wrong_record_count'):
            value=deepcopy(self.result)
            if change=='missing':value['files'].pop()
            elif change=='duplicate':value['files'][1]['record']['id']='11'
            elif change=='wrong_page':value['files'][1]['scope']['page']=1
            else:value['files'][1]['record']['rowCount']=2
            with self.subTest(change=change),self.assertRaises(ValueError):self.run_readback(value)

    def test_unknown_job_or_wrong_shop_not_fresh_export(self):
        self.job['state']='unknown'
        with self.assertRaisesRegex(ValueError,'not_finished'):self.run_readback()
        self.job['state']='finished';self.result['shop_name']='other'
        with self.assertRaisesRegex(ValueError,'request_mismatch'):self.run_readback()

    def test_changed_file_or_wrong_selected_page_not_accepted(self):
        value=deepcopy(self.result);value['files'][1]['path']=value['files'][0]['path']
        with self.assertRaisesRegex(ValueError,'changed'):self.run_readback(value)
        value['files'][1]['sha256']=value['files'][0]['sha256']
        with self.assertRaisesRegex(ValueError,'selected_page'):self.run_readback(value)

    def test_receipt_tampering_and_unconfigured_file_roots_rejected(self):
        self.run_readback()
        value=dict(self.result,evidence_path=str(self.path),observed_total=200)
        with self.assertRaisesRegex(ValueError,'observation_changed'):
            from_edge_job(dict(self.job,result=value),expected_request_id=self.request,expected_shop=self.shop,roots=[self.root])
        other=self.root/'other';other.mkdir()
        with self.assertRaisesRegex(ValueError,'outside_configured_roots'):
            from_edge_job(dict(self.job,result=value),expected_request_id=self.request,expected_shop=self.shop,roots=[other])
