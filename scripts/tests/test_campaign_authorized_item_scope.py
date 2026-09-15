import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from campaign_authorized_item_scope import validate,apply,AUTHORIZATION,WINDOWS

class AuthorizedItemScopeTests(unittest.TestCase):
    def authorization(self):return dict(authorization=AUTHORIZATION,items=['1001358847694'])
    def scope(self):return dict(erp_sellable=['1001358847694','863525290377','793052650673'],
        platform_rows=[dict(item=i,on_sale=True) for i in ['1001358847694','863525290377','793052650673']],
        sku_facts=[dict(item='1001358847694',sku='5991470332907'),dict(item='863525290377')],price_version='unchanged')
    def test_default_full_scope_unchanged(self):
        s=self.scope();self.assertIs(apply(s,None),s)
    def test_exact_subset_not_source_mutation(self):
        s=self.scope();before=copy.deepcopy(s);r=apply(s,self.authorization())
        self.assertEqual(r['erp_sellable'],['1001358847694']);self.assertEqual(s,before)
        self.assertEqual(r['sku_facts'],s['sku_facts']);self.assertEqual(r['price_version'],'unchanged')
    def test_all_three_authorized(self):
        a=self.authorization();a['items']=['1001358847694','793052650673','793202812082'];self.assertEqual(validate(a),a)
    def test_fee_link_and_extra_scope_rejected(self):
        for items in [[],['863525290377'],['other'],['1001358847694']*2,[1001358847694]]:
            a=self.authorization();a['items']=items
            with self.assertRaises(ValueError):validate(a)
    def test_wrong_authorization_or_unrecognized_field(self):
        a=self.authorization();a['authorization']='past'
        with self.assertRaises(ValueError):validate(a)
        a=self.authorization();a['override_price']=True
        with self.assertRaises(ValueError):validate(a)
    def test_exact_windows(self):
        plan=dict(segments=[dict(campaign=c,price_window=dict(start=s,end=e)) for c,s,e in WINDOWS])
        self.assertEqual(validate(self.authorization(),plan),self.authorization())
        plan['segments'][0]['price_window']['end']='2026-10-01 23:59:59'
        with self.assertRaises(ValueError):validate(self.authorization(),plan)
    def test_missing_erp_or_offsale_never_fake_complete(self):
        for key in ['erp_sellable','platform_rows']:
            s=self.scope();s[key]=[]
            with self.assertRaises(ValueError):apply(s,self.authorization())
    def test_repeated_scope_keeps_same_subset(self):
        r=apply(self.scope(),self.authorization());self.assertEqual(apply(r,self.authorization()),r)

if __name__=='__main__':unittest.main()
