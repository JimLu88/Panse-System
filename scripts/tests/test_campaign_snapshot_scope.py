import unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from copy import deepcopy
from types import SimpleNamespace
from campaign_price_snapshot import digest
from campaign_snapshot_scope import resolve_for_items


class ScopedSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.old = {'all_erp_rows':[dict(code='a',item='1',sku='11',daily='100'),
                                    dict(code='b',item='2',sku='22',daily='200')]}
        self.old['resolved_price_version_sha256']=digest(self.old['all_erp_rows'])
        self.new=deepcopy(self.old)
        self.auth=SimpleNamespace(resolve_snapshot=lambda _:deepcopy(self.new))

    def test_unrelated_rotation_cannot_change_transaction(self):
        self.new['all_erp_rows'][1].update(sku='23',alt=['22'])
        out=resolve_for_items(self.auth,self.old,['1'])
        self.assertEqual(out,self.old)

    def test_related_rotation_still_changes_digest(self):
        self.new['all_erp_rows'][0]['sku']='12'
        out=resolve_for_items(self.auth,self.old,['1'])
        self.assertNotEqual(out['resolved_price_version_sha256'],self.old['resolved_price_version_sha256'])
        self.assertEqual(self.old['all_erp_rows'][0]['sku'],'11')

    def test_related_price_still_changes_digest(self):
        self.new['all_erp_rows'][0]['daily']='101'
        self.assertNotEqual(resolve_for_items(self.auth,self.old,['1'])['resolved_price_version_sha256'],
                            self.old['resolved_price_version_sha256'])

    def test_alias_product_is_in_scope(self):
        for obj in (self.old,self.new):obj['all_erp_rows'][0]['product_alt_item_ids']=['3']
        self.new['all_erp_rows'][0]['sku']='12'
        self.assertEqual(resolve_for_items(self.auth,self.old,['3'])['all_erp_rows'][0]['sku'],'12')

    def test_identity_and_empty_scope_rejected(self):
        for change in ('code','item'):
            before=deepcopy(self.new)
            self.new['all_erp_rows'][1][change]='3'
            with self.assertRaises(ValueError):resolve_for_items(self.auth,self.old,['1'])
            self.new=before
        with self.assertRaises(ValueError):resolve_for_items(self.auth,self.old,[])
