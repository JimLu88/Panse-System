from copy import deepcopy
from io import BytesIO
import json
import re
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import campaign_official_template as tpl
import campaign_price_snapshot as snap
import campaign_generate_current_files as generate
from test_campaign_draft52 import fixture


def rate_fixture(reference, filled=''):
    raw = fixture()
    out = BytesIO()
    with ZipFile(BytesIO(raw)) as source, ZipFile(out, 'w') as dest:
        for part in source.infolist():
            data = source.read(part.filename)
            if part.filename == 'xl/worksheets/sheet1.xml':
                text = re.sub(r'(<c r="L4"[^>]*><is><t>).*?(</t>)', lambda m: m[1]+reference+m[2], data.decode())
                text = re.sub(r'(<c r="S4"[^>]*><is><t>).*?(</t>)', lambda m: m[1]+filled+m[2], text)
                data = text.encode()
            dest.writestr(part, data)
    return out.getvalue()


class CurrentRateTest(unittest.TestCase):
    rows = [{'item':'917179577721','sku':'6241018727158','activity_price':'100.00'}]

    def test_requires_explicit_rate(self):
        with self.assertRaises(TypeError):
            tpl.fill_selected_rows(fixture(), self.rows)

    def test_current_rates_and_package_identity(self):
        for rate in ['10%', '12%', '15%', '8%', '12.5%']:
            with self.subTest(rate=rate):
                raw = rate_fixture(rate)
                out = tpl.fill_selected_rows(raw, self.rows, official_rate=rate)
                self.assertEqual(tpl.read_rows(out, '商品SKU导入列表')[4]['S'], rate)
                with ZipFile(BytesIO(raw)) as a, ZipFile(BytesIO(out)) as b:
                    self.assertEqual(a.namelist(), b.namelist())
                    for name in a.namelist():
                        if name != 'xl/worksheets/sheet1.xml':
                            self.assertEqual(a.read(name), b.read(name))

    def test_fraction_reference_and_existing_percent(self):
        out = tpl.fill_selected_rows(rate_fixture('0.10', '10%'), self.rows, official_rate='0.100')
        self.assertEqual(tpl.read_rows(out,'商品SKU导入列表')[4]['S'], '10%')

    def test_blank_reference_is_not_twelve(self):
        out = tpl.fill_selected_rows(rate_fixture(''), self.rows, official_rate='15%')
        self.assertEqual(tpl.read_rows(out,'商品SKU导入列表')[4]['S'], '15%')

    def test_invalid_rates(self):
        for rate in [None, '', 'unknown', '10', '0', '100%', '-1%', 'NaN', 'Infinity', '1.234%']:
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                tpl.fill_selected_rows(rate_fixture(''), self.rows, official_rate=rate)

    def test_wrong_template_or_prefill_rejected(self):
        with self.assertRaisesRegex(ValueError, 'below_template'):
            tpl.fill_selected_rows(rate_fixture('12%'), self.rows, official_rate='10%')
        with self.assertRaisesRegex(ValueError, 'prefilled'):
            tpl.fill_selected_rows(rate_fixture('10%', '12%'), self.rows, official_rate='10%')


def super_fixture(state='异常'):
    headers = {'A':'商品ID','D':'商品状态','E':'SKUID','N':'活动价','O':'库存','P':'包邮','Q':'让利比例','R':'补贴金额','S':'商品短标题','Y':'短视频链接 1:1'}
    def row(n,values):
        return '<row r="'+str(n)+'">'+''.join('<c r="'+c+str(n)+'" s="1" t="inlineStr"><is><t>'+values.get(c,'')+'</t></is></c>' for c in 'ABCDEFGHIJKLMNOPQRSTUVWXY')+'</row>'
    data = row(1,{'A':'基础信息'})+row(2,headers)+row(3,{'Q':'填10，最多一位小数'})
    for n in range(4,8):
        data += row(n,{'A':'917179577721' if n==4 else '', 'D':state if n==4 else '', 'E':str(6241018727153+n),'O':'全部库存' if n==4 else '', 'P':'包邮' if n==4 else '', 'S':'保持短标题' if n==4 else '', 'T':'https://example.invalid/existing.jpg' if n==4 else ''})
    merges = '<mergeCells count="6">'+''.join('<mergeCell ref="'+c+'4:'+c+'7"/>' for c in ['A','D','O','P','Q','S'])+'</mergeCells>'
    xml = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><dimension ref="A1"/><sheetData>'+data+'</sheetData>'+merges+'</worksheet>'
    output = BytesIO()
    with ZipFile(BytesIO(fixture())) as source, ZipFile(output,'w') as dest:
        for part in source.infolist():
            dest.writestr(part,xml.encode() if part.filename=='xl/worksheets/sheet1.xml' else source.read(part.filename))
    return output.getvalue()


class SuperReduce25Test(unittest.TestCase):
    def test_correct_columns_numeric_rate_and_preserved_materials(self):
        raw=super_fixture()
        selected=[dict(item=r['item'],sku=r['sku'],activity_price='100.00') for r in tpl.template_rows(raw)]
        out=tpl.fill_selected_rows(raw,selected,official_rate='10%')
        rows=tpl.read_rows(out,'商品SKU导入列表')
        self.assertEqual(rows[4]['N'],'100.00')
        self.assertEqual(rows[4]['Q'],'10')
        self.assertEqual(rows[4]['R'],'')
        self.assertEqual(rows[4]['P'],'包邮')
        self.assertEqual(rows[4]['S'],'保持短标题')
        self.assertEqual(rows[4]['T'],'https://example.invalid/existing.jpg')
        with ZipFile(BytesIO(raw)) as a, ZipFile(BytesIO(out)) as b:
            for name in a.namelist():
                if name!='xl/worksheets/sheet1.xml':
                    self.assertEqual(a.read(name),b.read(name))
            self.assertIn('A1:Y7',b.read('xl/worksheets/sheet1.xml').decode())
            self.assertIn('r="Q4" s="1" t="n"><v>10</v>',b.read('xl/worksheets/sheet1.xml').decode())

    def test_published_and_rate_precision_rejected(self):
        for state in ['活动中','进行中','已发布设定','已生效']:
            raw=super_fixture(state)
            with self.subTest(state=state),self.assertRaisesRegex(ValueError,'must_not_replay'):
                tpl.fill_selected_rows(raw,CurrentRateTest.rows,official_rate='10%')
        with self.assertRaisesRegex(ValueError,'one_decimal'):
            tpl.fill_selected_rows(super_fixture(),CurrentRateTest.rows,official_rate='10.25%')


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.rows = [{'code':'EXAMPLE','item':'917179577721','sku':'6107353122531','alt':[], 'listing_status':'在售','daily':'4000.00','medium_target':None,'big_target':None,'custom':True}]
        self.receipt = {'official_success':True,'batch_id':'795728875','item_ids':['917179577721'],'new_sku_mapping':{'6107353122531':'6298567383791'}}

    def test_overlay_never_mutates_raw_and_keeps_current_prices(self):
        original = deepcopy(self.rows)
        result = snap.build_snapshot(self.rows, self.receipt)
        self.assertEqual(self.rows, original)
        self.assertEqual(result['all_erp_rows'][0]['sku'],'6298567383791')
        self.assertEqual(result['all_erp_rows'][0]['daily'],'4000.00')
        self.assertFalse(result['database_write'])
        self.assertFalse(result['no_sales_filter_applied'])

    def test_ambiguous_rotation_rejected(self):
        with self.assertRaisesRegex(ValueError,'not_unique'):
            snap.apply_rotation_receipt(self.rows*2, self.receipt)

    def test_unknown_and_unmapped_not_silently_dropped(self):
        self.rows.append({'code':'MISSING','listing_status':None})
        self.rows.append({'code':'UNMAPPED','listing_status':'在售'})
        result = snap.build_snapshot(self.rows)
        self.assertEqual(len(result['all_erp_rows']),3)
        self.assertEqual(result['unknown_listing_status_codes'], ['MISSING'])
        self.assertEqual(result['unmapped_sellable_codes'], ['UNMAPPED'])

    def test_one_readonly_transaction_without_old_prepare(self):
        with patch.object(snap.subprocess,'run') as run:
            run.return_value.stdout='BEGIN\n'+json.dumps(self.rows[0])+'\nCOMMIT\n'
            self.assertEqual(snap.load_rows(), self.rows)
            self.assertIn('REPEATABLE READ READ ONLY', run.call_args.kwargs['input'])
            self.assertNotIn('no_sales', run.call_args.kwargs['input'])
            self.assertEqual(run.call_count,1)


class SingleDiscountTemplateTest(unittest.TestCase):
    def setUp(self):
        self.raw = (Path(__file__).resolve().parents[2]/'backend/app/assets/taobao_templates/single_item_discount.xlsx').read_bytes()
        self.rows = [{'item':'917179577721','sku':str(6298567383791+n),'deduct':'10.25'} for n in range(4)]

    def test_examples_replaced_text_ids_and_parts_preserved(self):
        out = tpl.fill_single_discount_rows(self.raw,self.rows)
        rows = tpl.read_rows(out,'Sheet1')
        self.assertEqual(len(rows),5)
        for n,row in enumerate(self.rows,2):
            self.assertEqual(rows[n]['A'],row['item'])
            self.assertEqual(rows[n]['B'],row['sku'])
            self.assertEqual(rows[n]['C'],'10.25')
            self.assertEqual(rows[n].get('D',''),'')
        with ZipFile(BytesIO(self.raw)) as a, ZipFile(BytesIO(out)) as b:
            self.assertEqual(a.namelist(),b.namelist())
            for name in a.namelist():
                if name != 'xl/worksheets/sheet1.xml':
                    self.assertEqual(a.read(name),b.read(name))

    def test_duplicate_zero_empty_bad_template(self):
        for rows in [[],self.rows*2,[dict(self.rows[0],deduct='0')]]:
            with self.assertRaises(ValueError):
                tpl.fill_single_discount_rows(self.raw,rows)
        with self.assertRaisesRegex(ValueError,'columns_not_unique'):
            tpl.fill_single_discount_rows(fixture(),self.rows)


class TwoFilePriceRowsTest(unittest.TestCase):
    def setUp(self):
        self.erp=[{'item':'917179577721','sku':'6241018727158','alt':[],'code':'NORMAL','daily':'100.00','medium_target':'75.00','big_target':'70.00','custom':False}, {'item':'841201084787','sku':'6134306709526','alt':[],'code':'CUSTOM','daily':'1700.00','custom':True}]
        self.snapshot={'all_erp_rows':self.erp,'resolved_price_version_sha256':snap.digest(self.erp)}
        self.identities=[{'item':r['item'],'sku':r['sku'],'state':'草稿'} for r in self.erp]
        self.bases={('841201084787','6134306709526'):{'original':'1700.00','floor':'340.00','source':'verified-fixed-basis'}}

    def test_one_price_version_two_scopes(self):
        self.identities[0]['state']='活动中'
        activity,discounts,issues=generate.build_rows(self.snapshot,self.identities,tpl.discount_rate('10%'),'medium',self.bases)
        self.assertFalse(issues)
        self.assertEqual(len(activity),1)
        self.assertEqual(activity[0]['erp_code'],'CUSTOM')
        self.assertEqual(len(discounts),1)
        self.assertEqual(discounts[0]['deduct'],'15.00')
        self.assertEqual(discounts[0]['final'],'75.00')

    def test_big_target_same_for_twelve_and_fifteen(self):
        for rate,deduct in [('12%','18.00'),('15%','15.00')]:
            a,d,issues=generate.build_rows(self.snapshot,self.identities,tpl.discount_rate(rate),'big',self.bases)
            self.assertFalse(issues)
            self.assertEqual(a[0]['activity_price'],'100.00')
            self.assertEqual(d[0]['deduct'],deduct)
            self.assertEqual(d[0]['final'],'70.00')

    def test_custom_unchanged_daily_does_not_require_historical_basis_scan(self):
        a,d,issues=generate.build_rows(self.snapshot,self.identities,tpl.discount_rate('12%'),'big',{})
        self.assertFalse(issues)
        self.assertEqual(len(a),2)
        self.assertEqual(a[1]['activity_price'],'1700.00')
        self.assertIsNone(a[1]['custom_basis'])
        self.assertTrue(a[1]['basis_required_before_lowering'])

    def test_unknown_mapping_and_bad_formula_reported(self):
        self.identities.append({'item':'917179577721','sku':'9999999999999','state':'未报名'})
        self.erp[0]['big_target']='99.00'
        self.snapshot['resolved_price_version_sha256']=snap.digest(self.erp)
        a,d,issues=generate.build_rows(self.snapshot,self.identities,tpl.discount_rate('12%'),'big',self.bases)
        self.assertEqual({x['error'] for x in issues},{'price_formula_cannot_meet_frozen_target','erp_mapping_missing_or_not_unique'})

    def test_changed_snapshot_rejected(self):
        self.erp[0]['daily']='200.00'
        with self.assertRaisesRegex(ValueError,'snapshot_changed'):
            generate.build_rows(self.snapshot,self.identities,tpl.discount_rate('12%'),'big',self.bases)

    def test_explicit_scopes_are_independent_and_not_automatic_filters(self):
        self.identities[0]['state']='活动中'
        self.identities.append({'item':'999999999999','sku':'9999999999999','state':'未报名'})
        a,d,issues=generate.build_rows(self.snapshot,self.identities,tpl.discount_rate('10%'),'medium',{}, {'841201084787'}, {'917179577721'})
        self.assertFalse(issues)
        self.assertEqual([r['erp_code'] for r in a],['CUSTOM'])
        self.assertEqual([r['erp_code'] for r in d],['NORMAL'])
        _,_,issues=generate.build_rows(self.snapshot,self.identities,tpl.discount_rate('10%'),'medium',{})
        self.assertEqual(issues[0]['error'],'erp_mapping_missing_or_not_unique')

    def test_explicit_scope_cannot_invent_template_items(self):
        with self.assertRaisesRegex(ValueError,'explicit_scope_item_not_in_current_template'):
            generate.build_rows(self.snapshot,self.identities,tpl.discount_rate('10%'),'medium',{}, {'999999999999'}, set())


if __name__ == '__main__':
    unittest.main()
