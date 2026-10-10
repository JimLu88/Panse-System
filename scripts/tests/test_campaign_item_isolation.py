"""Synthetic local authority only; no network or production claims."""
from io import BytesIO
import json
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from campaign_entry_authority import Authority
from campaign_generate_current_files import _generate, isolate_item_issues
from campaign_price_snapshot import build_snapshot
from campaign_submission_gate import validated_body
from test_campaign_current_rate import rate_fixture

A, B = '917179577721', '917179577722'
START, END = '2026-10-20 20:00:00', '2026-11-13 23:59:59'


def two_items():
    out = BytesIO()
    with ZipFile(BytesIO(rate_fixture('15%', '15%'))) as source, ZipFile(out, 'w') as dest:
        for part in source.infolist():
            raw = source.read(part.filename)
            if part.filename == 'xl/worksheets/sheet1.xml':
                text = raw.decode()
                for col, value in [('A', B), ('D', '草稿'), ('L', '15%'), ('S', '15%')]:
                    text = re.sub(r'(<c r="'+col+r'6"[^>]*><is><t>).*?(</t>)',
                                  lambda m: m[1]+value+m[2], text)
                text = text.replace('count="5"', 'count="10"')
                for col in ('A', 'D', 'L', 'S', 'Q'):
                    text = text.replace(f'<mergeCell ref="{col}4:{col}7"/>',
                        f'<mergeCell ref="{col}4:{col}5"/><mergeCell ref="{col}6:{col}7"/>')
                raw = text.encode()
            dest.writestr(part, raw)
    return out.getvalue()


class IsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        manifest = self.root/'sources.json'; manifest.write_text('{"sources":[]}')
        self.auth = Authority(self.root/'test.sqlite3', manifest); self.addCleanup(self.auth.close)
        self.template = self.root/'template.xlsx'; self.template.write_bytes(two_items())
        self.rows = [dict(item=A if n<2 else B, sku=str(6241018727157+n), alt=[],
            code='CODE'+str(n), daily='100.00', medium_target='75.00', big_target='70.00', custom=False)
            for n in range(4)]

    def run_generation(self, *, isolate=True, name='out', rows=None, limits=False):
        snapshot = self.root/(name+'.json')
        snapshot.write_text(json.dumps(build_snapshot(self.rows if rows is None else rows)))
        args = SimpleNamespace(snapshot=snapshot, activity_template=self.template,
            output_dir=self.root/name, campaign_key='1/2/3', official_rate='15%', target='big',
            start=START, end=END, signup_items=None, discount_items=None,
            custom_basis_receipt=[], custom_corrections=None, isolate_item_issues=isolate,
            official_price_limits=limits)
        return _generate(args, self.auth)

    def assert_safe_a(self, result):
        self.assertEqual(result['status'], 'partial_local_files_ready_not_uploaded')
        self.assertEqual(result['item_isolation']['isolated_items'], [B])
        self.assertEqual(len(result['files']), 2)
        self.assertEqual({r['item'] for r in result['activity_rows']}, {A})
        self.assertEqual(len(result['activity_rows']), 2)
        body, _ = validated_body(self.auth, result['entry_bundle_id'], 'signup')
        self.assertEqual(body['signup_items'], [A])
        validated_body(self.auth, result['entry_bundle_id'], 'discount')

    def test_missing_mapping_isolates_whole_item_and_bundle_revalidates(self):
        self.assert_safe_a(self.run_generation(rows=self.rows[:-1]))

    def test_missing_price_isolates_whole_item(self):
        self.rows[-1]['big_target'] = None
        self.assert_safe_a(self.run_generation())

    def test_legacy_default_still_emits_no_files_on_issues(self):
        result = self.run_generation(isolate=False, rows=self.rows[:-1])
        self.assertFalse(result['files']); self.assertNotIn('entry_bundle_id', result)

    def test_all_items_bad_no_empty_bundle(self):
        result = self.run_generation(rows=[])
        self.assertFalse(result['files']); self.assertNotIn('entry_bundle_id', result)
        self.assertEqual(result['item_isolation']['isolated_items'], [A, B])

    def test_custom_null_targets_are_valid_no_single_discount(self):
        for r in self.rows[2:]: r.update(custom=True, medium_target=None, big_target=None)
        result = self.run_generation()
        self.assertFalse(result['issues'])
        self.assertEqual(len(result['activity_rows']), 4)
        self.assertEqual({r['item'] for r in result['discount_rows']}, {A})
        validated_body(self.auth, result['entry_bundle_id'], 'signup')

    def test_global_source_failure_is_not_isolated(self):
        with patch.object(self.auth, 'resolve_snapshot', side_effect=ValueError('source_changed')):
            with self.assertRaisesRegex(ValueError, 'source_changed'): self.run_generation()
        self.assertFalse((self.root/'out').exists())

    def test_unknown_signup_is_protected(self):
        original = self.auth.blocked
        with patch.object(self.auth, 'blocked', side_effect=lambda c,p,s,e,**kw: {B:'unknown'} if p=='signup' else original(c,p,s,e,**kw)):
            result = self.run_generation()
        self.assertEqual({r['item'] for r in result['activity_rows']}, {A})
        self.assertEqual(result['protected_scope']['signup'], {B:'unknown'})

    def test_unknown_discount_isolated_not_replayed(self):
        offer = dict(offer_id='UNKNOWN', start=START, end=END,
            items=[dict(item=B,status='unknown')], rows=[])
        with patch.object(self.auth, 'discount_offers', return_value=[offer]):
            result = self.run_generation()
            self.assert_safe_a(result)
        self.assertTrue(any(r['error']=='existing_discount_outcome_unknown' for r in result['issues']))

    def test_unscoped_issue_is_fatal(self):
        for issue in ({'error':'global'}, {'item':'OTHER','error':'x'}):
            with self.assertRaisesRegex(ValueError, 'unscoped_issue'):
                isolate_item_issues([issue], {A,B}, activity=[])

    def test_no_issue_emits_all_preserving_template_package(self):
        result = self.run_generation()
        self.assertEqual(len(result['activity_rows']), 4)
        path = next(f['path'] for f in result['files'] if Path(f['path']).name=='活动报名.xlsx')
        with ZipFile(self.template) as src, ZipFile(path) as dst:
            self.assertEqual(src.namelist(), dst.namelist())
            for name in src.namelist():
                if name!='xl/worksheets/sheet1.xml': self.assertEqual(src.read(name),dst.read(name))

    def test_output_directory_not_overwritten(self):
        self.run_generation()
        with self.assertRaisesRegex(ValueError, 'output_exists'): self.run_generation()

    def test_known_custom_floor_is_not_bypassed(self):
        for r in self.rows[2:]: r.update(custom=True, medium_target=None, big_target=None)
        with patch.object(self.auth, 'bases', return_value={(B,self.rows[2]['sku']):{'floor':'101'}}):
            result = self.run_generation()
            self.assert_safe_a(result)

    def set_limits(self, *, price='100', final='70', suggestion='100'):
        output=BytesIO()
        with ZipFile(self.template) as src, ZipFile(output,'w') as dst:
            for part in src.infolist():
                raw=src.read(part.filename)
                if part.filename=='xl/worksheets/sheet1.xml':
                    text=raw.decode()
                    for n in [2,4,5,6,7]:
                        values={'H':'最低标价','I':'最低普惠券后价要求','K':'符合要求的建议价'} if n==2 else {'H':price,'I':final,'K':suggestion}
                        for col,value in values.items():
                            text=re.sub(r'(<c r="'+col+str(n)+r'"[^>]*><is><t>).*?(</t>)',lambda m:m[1]+value+m[2],text)
                    raw=text.encode()
                dst.writestr(part,raw)
        self.template.write_bytes(output.getvalue())

    def test_official_caps_revalidate_within_two_yuan(self):
        self.set_limits(final='69.5')
        result=self.run_generation(limits=True)
        self.assertFalse(result['issues'])
        self.assertEqual({r['final'] for r in result['discount_rows']},{'69.50'})
        self.assertEqual({r['target'] for r in result['discount_rows']},{'70.00'})
        validated_body(self.auth,result['entry_bundle_id'],'discount')
        validated_body(self.auth,result['entry_bundle_id'],'signup')

    def test_cap_exceeds_two_never_ratchets_target(self):
        self.set_limits(final='67.99')
        result=self.run_generation(limits=True)
        self.assertFalse(result['files'])
        self.assertTrue(all('requires_rotation' in r['error'] for r in result['issues']))

    def test_daily_above_price_cap_not_lowered(self):
        self.set_limits(price='99')
        result=self.run_generation(limits=True)
        self.assertFalse(result['files'])
        self.assertTrue(all(r['error']=='erp_daily_exceeds_official_price_cap_requires_rotation' for r in result['issues']))

    def test_blank_cap_not_zero(self):
        self.set_limits(final='')
        result=self.run_generation(limits=True)
        self.assertFalse(result['files'])
        self.assertTrue(all(r['error']=='current_official_price_limits_missing' for r in result['issues']))

    def test_custom_official_lowering_requires_fixed_floor(self):
        for row in self.rows: row.update(custom=True,medium_target=None,big_target=None)
        self.set_limits(price='90',final='76.5',suggestion='90')
        basis={(r['item'],r['sku']):{'floor':'20','original':'100','uncertain':False,'source':'synthetic-test'} for r in self.rows}
        with patch.object(self.auth,'bases',return_value=basis):
            result=self.run_generation(limits=True)
            self.assertFalse(result['issues'])
            self.assertEqual({r['activity_price'] for r in result['activity_rows']},{'90'})
            self.assertFalse(result['discount_rows'])
            validated_body(self.auth,result['entry_bundle_id'],'signup')

    def test_custom_floor_unknown_isolated(self):
        for row in self.rows: row.update(custom=True,medium_target=None,big_target=None)
        self.set_limits(price='90',final='76.5',suggestion='90')
        result=self.run_generation(limits=True)
        self.assertFalse(result['files'])
        self.assertTrue(all(r['error']=='custom_fixed_original_missing_or_uncertain' for r in result['issues']))

    def test_export_binding_unique_code_keeps_money_and_primary(self):
        from campaign_generation_export import bind_rows
        before=self.rows[0].copy()
        rows=bind_rows(self.rows,[dict(item=A,sku='9999999999999',merchant_code='CODE0')])
        self.assertEqual(rows[0]['alt'],['9999999999999'])
        self.assertEqual(rows[0]['sku'],before['sku'])
        self.assertEqual(rows[0]['daily'],before['daily'])
        self.assertEqual(self.rows[0],before)

    def test_export_binding_cross_product_and_suffix_not_guessed(self):
        from campaign_generation_export import bind_rows
        rows=bind_rows(self.rows,[dict(item=B,sku='9999999999999',merchant_code='CODE0'),
            dict(item=A,sku='9999999999998',merchant_code='CODE0B1')])
        self.assertEqual(rows,self.rows)


if __name__ == '__main__': unittest.main()
