from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from xml.sax.saxutils import escape
from zipfile import ZipFile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import campaign_official_template as tpl
import campaign_prepare_draft52 as prep


def fixture(extra='', dimension='A1'):
    headers={'A':'商品ID','D':'商品状态','E':'SKUID','L':'官方立减默认折扣','P':'活动价','S':'官方立减报名折扣','T':'官方立减金额'}
    def row(n, values):
        return '<row r="'+str(n)+'">'+''.join('<c r="'+col+str(n)+'" s="1" t="inlineStr"><is><t>'+escape(values.get(col,''))+'</t></is></c>' for col in 'ABCDEFGHIJKLMNOPQRST')+'</row>'
    data=row(1,{'A':'基础信息'})+row(2,headers)+row(3,{'A':'必填'})
    for n in range(4,8):
        data+=row(n,{'A':'917179577721' if n==4 else '', 'D':'草稿' if n==4 else '', 'E':str(6241018727153+n), 'L':'12%' if n==4 else '', 'S':'12%' if n==4 else ''})
    merges='<mergeCells count="5">'+''.join('<mergeCell ref="'+c+'4:'+c+'7"/>' for c in ('A','D','L','S','Q'))+'</mergeCells>'
    xml='<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><dimension ref="'+dimension+'"/><sheetData>'+data+'</sheetData>'+merges+extra+'</worksheet>'
    buf=BytesIO()
    with ZipFile(buf,'w') as z:
        z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="商品SKU导入列表" r:id="r1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<Relationships><Relationship Id="r1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr('docProps/custom.xml','<Properties><property name="property1"><value>TEST-ONLY</value></property></Properties>')
        z.writestr('xl/worksheets/sheet1.xml',xml)
        z.writestr('xl/styles.xml','unchanged-test-style')
    return buf.getvalue()


class OfficialTemplateTest(unittest.TestCase):
    def setUp(self):
        self.raw=fixture()
        self.selected=[{'item':'917179577721','sku':'6241018727158','activity_price':'100.00'}, {'item':'917179577721','sku':'6241018727160','activity_price':'200.00'}]

    def test_physical_rows_ignore_false_dimension(self):
        self.assertEqual(len(tpl.template_rows(self.raw)),4)

    def test_projection_preserves_identity_anchor(self):
        out=tpl.fill_selected_rows(self.raw,self.selected)
        self.assertEqual([(r['item'],r['sku']) for r in tpl.template_rows(out)],[(r['item'],r['sku']) for r in self.selected])

    def test_merge_compaction_and_no_orphan(self):
        out=tpl.fill_selected_rows(self.raw,self.selected)
        with ZipFile(BytesIO(out)) as z:
            xml=z.read('xl/worksheets/sheet1.xml').decode()
            self.assertIn('A4:A5',xml)
            self.assertNotIn('A4:A7',xml)
            self.assertIn('A1:T5',xml)

    def test_non_data_parts_identical(self):
        with ZipFile(BytesIO(self.raw)) as a, ZipFile(BytesIO(tpl.fill_selected_rows(self.raw,self.selected))) as b:
            self.assertEqual(a.namelist(),b.namelist())
            for name in a.namelist():
                if name!='xl/worksheets/sheet1.xml':
                    self.assertEqual(a.read(name),b.read(name))

    def test_prices_and_existing_percentage_preserved(self):
        rows=tpl.read_rows(tpl.fill_selected_rows(self.raw,self.selected),'商品SKU导入列表')
        self.assertEqual(rows[4]['P'],'100.00')
        self.assertEqual(rows[5]['P'],'200.00')
        self.assertEqual(rows[4]['S'],'12%')
        self.assertEqual(rows[4]['T'],'')

    def test_duplicate_selected_rejected(self):
        with self.assertRaisesRegex(ValueError,'duplicate'):
            tpl.fill_selected_rows(self.raw,self.selected*2)

    def test_missing_pair_rejected(self):
        self.selected[0]['sku']='9999999999999'
        with self.assertRaisesRegex(ValueError,'missing'):
            tpl.fill_selected_rows(self.raw,self.selected)

    def test_formula_features_not_silently_corrupted(self):
        with self.assertRaisesRegex(ValueError,'unsupported_row_bound'):
            tpl.fill_selected_rows(fixture('<dataValidations/>'),self.selected)

    def test_unsafe_money_rejected(self):
        for price in ('NaN','Infinity','-1','1.001','0'):
            with self.subTest(price=price),self.assertRaises(ValueError):
                tpl.fill_selected_rows(self.raw,[dict(self.selected[0],activity_price=price)])

    def test_self_closing_cell_does_not_consume_next_cell(self):
        xml='<row r="4"><c r="P4" s="1"/><c r="Q4" t="inlineStr"><is><t>全部库存</t></is></c></row>'
        changed=tpl._set_cell(xml,'P',4,'100.00')
        self.assertIn('<c r="Q4" t="inlineStr"><is><t>全部库存</t></is></c>',changed)

    def test_empty_signup_percentage_written_as_literal_percent(self):
        xml='<row r="4"><c r="S4" s="1"/><c r="T4"/></row>'
        changed=tpl._set_percent_text(xml,4)
        self.assertIn('<t>12%</t>',changed)
        self.assertIn('<c r="T4"/>',changed)


class PriceScopeTest(unittest.TestCase):
    def setUp(self):
        self.scope={'ordinary_scope':[{'item_id':'917179577721','sku_id':'6241018727157','legacy_deduct':'10.00'}]}
        self.erp=[{'item':'917179577721','sku':'6241018727157','code':'EXAMPLE','daily':'100.00','target':'78.00','custom':False}]

    def test_exact_target(self):
        rows,issues=prep.build_price_rows(self.scope,self.erp)
        self.assertFalse(issues)
        self.assertEqual(rows[0]['activity_price'],'100.00')

    def test_current_positive_two_yuan_allowed(self):
        self.erp[0]['target']='76.00'
        rows,issues=prep.build_price_rows(self.scope,self.erp)
        self.assertFalse(issues)
        self.assertEqual(rows[0]['positive_delta_authorized_this_time'],'2.00')

    def test_below_target_forbidden(self):
        self.erp[0]['target']='78.01'
        self.assertEqual(prep.build_price_rows(self.scope,self.erp)[0],[])

    def test_above_two_forbidden(self):
        self.erp[0]['target']='75.99'
        self.assertEqual(prep.build_price_rows(self.scope,self.erp)[0],[])

    def test_custom_never_included(self):
        self.erp[0]['custom']=True
        self.assertEqual(prep.build_price_rows(self.scope,self.erp)[0],[])

    def test_mapping_duplicates_forbidden(self):
        self.assertEqual(prep.build_price_rows(self.scope,self.erp*2)[0],[])

    def test_erp_readonly_sql_uses_stdin_without_remote_quote_interpolation(self):
        with patch.object(prep.subprocess,'run') as run:
            run.return_value.stdout='BEGIN\n'+json.dumps(self.erp[0])+'\nCOMMIT\n'
            self.assertEqual(prep.load_erp_snapshot(self.scope),self.erp)
            command=run.call_args.args[0][-1]
            self.assertNotIn('SELECT',command)
            self.assertTrue(run.call_args.kwargs['input'].startswith('BEGIN READ ONLY;'))
            self.assertEqual(run.call_count,1)

    def test_uploaded_continuation_stops_before_file_or_network_reads(self):
        with patch.object(prep,'json_read',return_value={'submission_checkpoint':{'new_generation_allowed':False}}), patch.object(prep,'load_erp_snapshot') as load:
            with self.assertRaisesRegex(ValueError,'already_uploaded'):
                prep.prepare(Path('missing-template'),Path('missing-discount'),Path('unused-output'))
            load.assert_not_called()


if __name__=='__main__':
    unittest.main()
