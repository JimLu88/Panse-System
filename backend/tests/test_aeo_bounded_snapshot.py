"""Standalone SQLite fixtures; never imports ERP database/config or private data."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from sqlalchemy import create_engine, event, text

spec=importlib.util.spec_from_file_location('bounded_export',Path(__file__).resolve().parents[1]/'scripts/export_aeo_bounded_snapshot.py')
export=importlib.util.module_from_spec(spec);spec.loader.exec_module(export)


class BoundedTests(unittest.TestCase):
    def setUp(self):
        self.engine=create_engine('sqlite:///:memory:')
        self.c=self.engine.connect()
        self.c.execute(text('''CREATE TABLE products(code TEXT,name TEXT,sub_name TEXT,brand TEXT,
            category TEXT,image_url TEXT,main_material TEXT,aux_material TEXT,size_detail TEXT,
            size_value TEXT,size_confirmed TEXT,listing_status TEXT,updated_at TEXT, supplier TEXT)'''))
        self.c.execute(text('''CREATE TABLE pricing_sku(product_code TEXT,sku_code TEXT,sku TEXT,
            size_info TEXT,image_url TEXT,updated_at TEXT,is_custom_placeholder BOOLEAN,
            daily_price NUMERIC, accounting_cost NUMERIC)'''))
        self.c.execute(text('''CREATE TABLE pricing_sku_promo(sku_code TEXT,mid_buyer_price NUMERIC,
            updated_at TEXT,profit NUMERIC)'''))
        for p,s in [('P1','S1'),('P1','S2'),('P2','S3')]:
            if s!='S2':
                self.c.execute(text("INSERT INTO products(code,name,updated_at,supplier) VALUES(:p,:p,'2026-10-09 01:00:00','PRIVATE-SUPPLIER')"),dict(p=p))
            self.c.execute(text("INSERT INTO pricing_sku VALUES(:p,:s,'public variant','250x400',NULL,'2026-10-09 01:00:00',FALSE,9999,8888)"),dict(p=p,s=s))
            self.c.execute(text("INSERT INTO pricing_sku_promo VALUES(:s,123.45,'2026-10-09 02:00:00',7777)"),dict(s=s))
        self.c.commit()
        self.scope=dict(requestedProductCodes=['P1'],requestedSkuCodes=['S1'],
                        acceptedProductSkuMappings=[dict(productCode='P1',skuCode='S1')])
        self.sql=[]
        event.listen(self.c,'before_cursor_execute',lambda conn,cursor,statement,params,context,many:self.sql.append(statement))

    def tearDown(self):
        self.c.close();self.engine.dispose()

    def test_projection_and_scope(self):
        value=export.build_snapshot(self.c,self.scope)
        self.assertEqual(value['counts'],dict(products=1,pricing=1,dimensions=0))
        self.assertEqual(value['pricing'][0]['midBuyerPrice'],'123.45')
        self.assertEqual(value['scope']['coverage'],'selected-product-sku-only')
        for forbidden in ['PRIVATE-SUPPLIER','accounting_cost','dailyPrice','profit','S2','P2','S3']:
            self.assertNotIn(forbidden,json.dumps(value))
        for statement in self.sql:
            for field in ['accounting_cost','supplier','profit','daily_price','SELECT *']:
                self.assertNotIn(field,statement)
        self.assertIn('WHERE code IN',self.sql[1])
        self.assertIn('FROM requested',self.sql[2])
        self.assertNotIn('app.database',sys.modules)
        self.assertTrue(export.validate_snapshot(value,self.scope))

    def test_empty_scope_before_sql(self):
        for bad in [{},dict(self.scope,requestedProductCodes=[]),dict(self.scope,requestedSkuCodes=[])]:
            with self.assertRaises(ValueError):export.build_snapshot(self.c,bad)
        self.assertEqual(self.sql,[])

    def test_unknown_product(self):
        self.scope['requestedProductCodes']=['X'];self.scope['acceptedProductSkuMappings'][0]['productCode']='X'
        with self.assertRaisesRegex(ValueError,'product_missing'):export.build_snapshot(self.c,self.scope)

    def test_cross_product_sku(self):
        self.scope['requestedSkuCodes']=['S3'];self.scope['acceptedProductSkuMappings'][0]['skuCode']='S3'
        with self.assertRaisesRegex(ValueError,'mapping_mismatch'):export.build_snapshot(self.c,self.scope)

    def test_unknown_sku(self):
        self.scope['requestedSkuCodes']=['X'];self.scope['acceptedProductSkuMappings'][0]['skuCode']='X'
        with self.assertRaisesRegex(ValueError,'mapping_mismatch'):export.build_snapshot(self.c,self.scope)

    def test_custom_excluded(self):
        self.c.execute(text("UPDATE pricing_sku SET is_custom_placeholder=TRUE WHERE sku_code='S1'"));self.c.commit()
        with self.assertRaises(ValueError):export.build_snapshot(self.c,self.scope)

    def test_duplicate_rejected(self):
        self.scope['requestedSkuCodes']=['S1','S1']
        with self.assertRaises(ValueError):export.build_snapshot(self.c,self.scope)
        self.assertEqual(self.sql,[])

    def test_missing_promo_timestamp(self):
        self.c.execute(text("DELETE FROM pricing_sku_promo WHERE sku_code='S1'"));self.c.commit()
        with self.assertRaisesRegex(ValueError,'timestamp_missing'):export.build_snapshot(self.c,self.scope)

    def test_null_price_not_fabricated(self):
        self.c.execute(text("UPDATE pricing_sku_promo SET mid_buyer_price=NULL WHERE sku_code='S1'"));self.c.commit()
        self.assertIsNone(export.build_snapshot(self.c,self.scope)['pricing'][0]['midBuyerPrice'])

    def test_read_only_enforced(self):
        export.build_snapshot(self.c,self.scope)
        with self.assertRaises(Exception):self.c.execute(text("UPDATE products SET name='changed'"))

    def test_extra_requested_mapping(self):
        self.scope['requestedSkuCodes'].append('S2')
        self.scope['narrowAdditionalReadScope']=dict(productCode='P1',skuCode='S2',heightAccepted=False)
        self.assertEqual(export.build_snapshot(self.c,self.scope)['counts']['pricing'],2)

    def test_unknown_extra_mapping_fails_before_query(self):
        self.scope['acceptedProductSkuMappings'].append(dict(productCode='P2',skuCode='S3'))
        with self.assertRaises(ValueError):export.build_snapshot(self.c,self.scope)
        self.assertEqual(self.sql,[])

    def test_bad_code_cannot_inject_sql(self):
        self.scope['requestedProductCodes']=["P1'); DROP TABLE products; --"]
        with self.assertRaises(ValueError):export.build_snapshot(self.c,self.scope)
        self.assertEqual(self.sql,[])

    def test_original_a17_fixture(self):
        fixture=Path(__file__).resolve().parents[2]/'docs/fixtures/aeo-a17-scope.json'
        pairs=export.scope_pairs(json.loads(fixture.read_text(encoding='utf-8')))
        self.assertEqual(len(pairs),5);self.assertEqual(len({p for p,s in pairs}),4)

    def test_validator_rejects_private_extra(self):
        value=export.build_snapshot(self.c,self.scope)
        value['pricing'][0]['accounting_cost']=123
        with self.assertRaisesRegex(ValueError,'non_public'):export.validate_snapshot(value,self.scope)

    def test_validator_rejects_nested_private(self):
        value=export.build_snapshot(self.c,self.scope)
        value['products'][0]['name']={'cost':123}
        with self.assertRaisesRegex(ValueError,'non_public'):export.validate_snapshot(value,self.scope)

    def test_validator_rejects_out_of_scope(self):
        value=export.build_snapshot(self.c,self.scope)
        value['pricing'][0]['skuCode']='S2'
        with self.assertRaisesRegex(ValueError,'exact_scope'):export.validate_snapshot(value,self.scope)

    def test_validator_rejects_daily_as_mid(self):
        value=export.build_snapshot(self.c,self.scope)
        value['pricing'][0]['sourceField']='PricingSku.daily_price'
        with self.assertRaisesRegex(ValueError,'semantics'):export.validate_snapshot(value,self.scope)

    def test_validator_rejects_private_source_metadata(self):
        value=export.build_snapshot(self.c,self.scope);value['source']['password']='secret'
        with self.assertRaisesRegex(ValueError,'source_contract'):export.validate_snapshot(value,self.scope)

    def test_validator_rejects_scope_metadata_drift(self):
        value=export.build_snapshot(self.c,self.scope);value['scope']['requestedSkuCodes'].append('S2')
        with self.assertRaisesRegex(ValueError,'scope_metadata'):export.validate_snapshot(value,self.scope)


if __name__=='__main__':unittest.main()
