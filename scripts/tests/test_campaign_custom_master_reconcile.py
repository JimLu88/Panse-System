import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import campaign_custom_master_reconcile as repair


class CustomMasterRepairTests(unittest.TestCase):
    def scope(self):
        return dict(complete=True,platform_rows=[dict(item=repair.ITEM,on_sale=True)],
            sku_facts=[dict(facts=dict(item=repair.ITEM,sku=s,sku_code='',
                attributes='颜色分类:'+n+';',price=p),sources=[dict(sha256=repair.SOURCE_SHA)])
                for s,c,n,p,on,op in repair.ROWS])

    def state(self):
        return dict(products=[dict(code=repair.CODE,taobao_id=repair.ITEM,listing_status='在售')],
            receipts=[dict(authorization='user-20260915-custom-master-taobao-price',official_source_sha256=repair.SOURCE_SHA)],
            rows=[dict(sku_code=c,taobao_sku_id=s,taobao_item_id=repair.ITEM,
                sku=n,daily_price=p,is_custom_placeholder=True) for s,c,n,p,on,op in repair.ROWS])

    def test_exact_platform_prices(self):
        rows=repair.validate_scope(self.scope())
        self.assertEqual([r['price'] for r in rows],['2000.00','5000.00','9000.00','13000.00','20000.00'])

    def test_missing_or_duplicate_sku_rejected(self):
        for mutate in (lambda a:a.pop(),lambda a:a.__setitem__(0,copy.deepcopy(a[1]))):
            s=self.scope();mutate(s['sku_facts'])
            with self.assertRaises(ValueError):repair.validate_scope(s)

    def test_price_and_name_and_source_drift_rejected(self):
        for field,value in [('price','20000'),('attributes','颜色分类:其他;'),('sku_code','invented')]:
            s=self.scope();s['sku_facts'][0]['facts'][field]=value
            with self.assertRaises(ValueError):repair.validate_scope(s)
        s=self.scope();s['sku_facts'][0]['sources'][0]['sha256']='fake'
        with self.assertRaises(ValueError):repair.validate_scope(s)

    def test_not_on_sale_and_incomplete_rejected(self):
        s=self.scope();s['complete']=False
        with self.assertRaises(ValueError):repair.validate_scope(s)
        s=self.scope();s['platform_rows'][0]['on_sale']=False
        with self.assertRaises(ValueError):repair.validate_scope(s)

    def test_other_product_never_in_payload(self):
        s=self.scope();s['sku_facts'].append(dict(facts=dict(item='863525290377',sku='1')))
        self.assertEqual(len(repair.validate_scope(s)),5)
        sql=repair.build_sql(repair.validate_scope(s),'abc')
        self.assertNotIn('863525290377',sql)

    def test_sql_transaction_audit_cas_and_one_master(self):
        sql=repair.build_sql(repair.validate_scope(self.scope()),'abc')
        for guard in ['SERIALIZABLE','pg_advisory_xact_lock','FOR UPDATE',
                      'old_prices_or_names_changed_no_overwrite','exact_existing_five_sku_mapping_changed',
                      'committed_repair_drift_no_overwrite','INSERT INTO audit_logs']:
            self.assertIn(guard,sql)
        self.assertEqual(sql.count('INSERT INTO products'),1)
        for forbidden in ['INSERT INTO pricing_sku','DELETE FROM','UPDATE pricing_sku_promo','UPDATE orders']:
            self.assertNotIn(forbidden,sql)
        self.assertLess(sql.index('INSERT INTO audit_logs'),sql.index('COMMIT;'))

    def test_readback_requires_all_exact_prices(self):
        s=self.state();self.assertTrue(repair.readback_verified(s))
        s['rows'][0]['daily_price']='20000.00'
        self.assertFalse(repair.readback_verified(s))

    def test_readback_requires_one_master_and_receipt(self):
        for key in ['products','receipts','rows']:
            s=self.state();s[key].pop()
            self.assertFalse(repair.readback_verified(s))
        s=self.state();s['products'].append(copy.deepcopy(s['products'][0]))
        self.assertFalse(repair.readback_verified(s))

    def test_readback_preserves_custom_identity(self):
        for key,value in [('taobao_sku_id','different'),('is_custom_placeholder',False),('taobao_item_id','other')]:
            s=self.state();s['rows'][0][key]=value
            self.assertFalse(repair.readback_verified(s))

    def test_sql_literals_escaped(self):
        self.assertEqual(repair.literal("a'b"),"'a''b'")


if __name__=='__main__':unittest.main()
