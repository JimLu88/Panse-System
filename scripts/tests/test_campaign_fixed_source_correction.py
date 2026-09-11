"""Exact floor repair leaves original price, source history and claims untouched."""
from copy import deepcopy
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
import unittest
import test_campaign_entry_guards as entry
from campaign_entry_authority import Authority, file_sha, load, validate_price
from campaign_price_snapshot import build_snapshot


class FixedSourceCorrectionTests(unittest.TestCase):
    setUp=entry.EntryTests.setUp
    write=entry.EntryTests.write
    register=entry.EntryTests.register

    def fixture(self):
        row=dict(item=entry.ITEM,sku=entry.SKU,erp_code='NORMAL',fixed_original_record='541.67',fixed_floor='108.33')
        old=self.write('bad.json',dict(schema='campaign_user_established_fixed_baseline_v1',rows=[row]))
        # Reproduce the legacy invalid registration in this isolated test DB.
        self.auth.db.execute('INSERT INTO sources VALUES(?,?,?)',(str(old),'fixed',file_sha(old)))
        corrected=dict(schema='campaign_user_established_fixed_baseline_v1',rows=[dict(row,fixed_floor='108.334')],
            supersedes_receipt=str(old),correction_reason='Fix precision only')
        new=self.write('corrected.json',corrected)
        return old,new

    def test_precision_replacement_preserves_history_and_is_idempotent(self):
        old,new=self.fixture();snap=build_snapshot([dict(self.row,daily='541.67',custom=True)])
        self.assertFalse(self.auth.bases(snap))
        before=file_sha(old)
        result=self.auth.correct_fixed_floor_source(old,before,new,file_sha(new))
        self.assertEqual(len(result['changed_rows']),1)
        self.assertEqual(file_sha(old),before)
        self.assertEqual(self.auth.db.execute('SELECT count(*) FROM sources').fetchone()[0],2)
        self.assertEqual(len(self.auth.sources()),1)
        basis=self.auth.bases(snap)[(entry.ITEM,entry.SKU)]
        self.assertFalse(basis['uncertain']);self.assertEqual(basis['floor'],'108.334')
        validate_price(dict(custom=True,activity_price='108.34'),'541.67',basis,lowering_authorized=True,failed_exact=True)
        with self.assertRaisesRegex(ValueError,'twenty_percent'):
            validate_price(dict(custom=True,activity_price='108.33'),'541.67',basis,lowering_authorized=True,failed_exact=True)
        self.assertTrue(self.auth.correct_fixed_floor_source(old,before,new,file_sha(new))['idempotent'])
        self.assertEqual(self.auth.db.execute('SELECT count(*) FROM attempts').fetchone()[0],0)

    def test_bad_precision_cannot_be_registered_again(self):
        bad=self.write('new-bad.json',dict(rows=[dict(item=entry.ITEM,sku=entry.SKU,erp_code='NORMAL',fixed_original_record='541.67',fixed_floor='108.33')]))
        with self.assertRaisesRegex(ValueError,'invalid_fixed_custom_basis'):
            self.auth.register_source(bad,'fixed',file_sha(bad))
        self.assertEqual(self.auth.db.execute('SELECT count(*) FROM sources').fetchone()[0],0)

    def test_scope_original_authority_and_rounded_up_math_floor_rejected(self):
        old,new=self.fixture();doc=load(new)
        variants=[]
        for field,value in [('fixed_original_record','500'),('fixed_floor','108.34'),('sku','OTHER')]:
            d=deepcopy(doc);d['rows'][0][field]=value;variants.append(d)
        variants.append(dict(doc,user_verbatim='different'))
        variants.append(dict(doc,rows=doc['rows']*2))
        for i,variant in enumerate(variants):
            path=self.write(f'bad-new-{i}.json',variant)
            with self.assertRaises(ValueError):self.auth.correct_fixed_floor_source(old,file_sha(old),path,file_sha(path))
        self.assertEqual(self.auth.db.execute('SELECT count(*) FROM fixed_source_corrections').fetchone()[0],0)

    def test_default_and_registered_duplicate_has_same_source_fingerprint(self):
        old,new=self.fixture();self.auth.correct_fixed_floor_source(old,file_sha(old),new,file_sha(new))
        snap=build_snapshot([self.row]);before=self.auth.resolve_snapshot(snap)['entry_source_sha256']
        self.auth.config['sources']=[dict(path=str(new),kind='fixed',sha256=file_sha(new))]
        self.assertEqual(before,self.auth.resolve_snapshot(snap)['entry_source_sha256'])
        self.assertEqual(len(self.auth.sources()),1)

    def test_audit_file_mutation_is_not_silently_ignored(self):
        old,new=self.fixture();self.auth.correct_fixed_floor_source(old,file_sha(old),new,file_sha(new))
        old.write_text('{}')
        with self.assertRaisesRegex(ValueError,'audit_changed'):self.auth.sources()

    def test_actual_78_plus_four_exact_floor_and_later_price_no_rebase(self):
        repo=Path(__file__).resolve().parents[2]
        new=repo/'docs/receipts/campaign-78-custom-user-established-baseline-20260911-v2.json'
        four=repo/'docs/receipts/campaign-four-custom-user-established-baseline-20260911.json'
        records=load(new)['rows']+load(four)['rows']
        self.assertEqual(len(records),82)
        for source in (new,four):self.auth.register_source(source,'fixed',file_sha(source))
        rows=[dict(item=r['item'],sku=r['sku'],code=r['erp_code'],daily=r['fixed_original_record'],custom=True,alt=[]) for r in records]
        # Fresh authority instance loads the persistent source catalog without flags.
        other=Authority(self.root/'state.sqlite3',self.manifest)
        try:
            bases=other.bases(build_snapshot(rows))
            self.assertEqual(len(bases),82)
            for row in rows:
                basis=bases[(row['item'],row['sku'])]
                self.assertFalse(basis['uncertain'])
                floor=Decimal(basis['floor']);self.assertEqual(floor,Decimal(row['daily'])*Decimal('.20'))
                minimum=floor.quantize(Decimal('.01'),rounding=ROUND_CEILING)
                validate_price(dict(custom=True,activity_price=str(minimum)),row['daily'],basis,lowering_authorized=True,failed_exact=True)
                with self.assertRaises(ValueError):
                    validate_price(dict(custom=True,activity_price=str(minimum-Decimal('.01'))),row['daily'],basis,lowering_authorized=True,failed_exact=True)
                row['daily']=str(minimum)
            later=other.bases(build_snapshot(rows))
            self.assertEqual(bases,later)
            # Synthetic verified alias check, never a real SKU creation.
            replacement=dict(rows[0],sku='TEST-REPLACEMENT',alt=[rows[0]['sku']])
            inherited=other.bases(build_snapshot([replacement]))[(replacement['item'],'TEST-REPLACEMENT')]
            self.assertEqual(inherited['original'],bases[(rows[0]['item'],rows[0]['sku'])]['original'])
        finally:other.close()


if __name__=='__main__':unittest.main()
