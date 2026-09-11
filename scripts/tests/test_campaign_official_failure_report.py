import hashlib
from io import BytesIO
import unittest
from pathlib import Path
import sys
from xml.sax.saxutils import escape
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from campaign_official_failure_report import parse_report


def package(data):
    header = ['商品ID', 'SKUID', 'SKU名称', '活动价', '是否成功', '失败原因或风险提示']
    rows = []
    for n, values in enumerate([header] + data, 1):
        cells = ''.join(f'<c r="{chr(65+c)}{n}" t="inlineStr"><is><t>{escape(v)}</t></is></c>'
                        for c, v in enumerate(values))
        rows.append(f'<row r="{n}">{cells}</row>')
    out = BytesIO()
    with ZipFile(out, 'w') as z:
        z.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="商品SKU导入列表" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><dimension ref="A1"/><sheetData>'+''.join(rows)+'</sheetData></worksheet>')
    return out.getvalue()


class ReportTests(unittest.TestCase):
    def parse(self, rows, items=('1',)):
        raw = package(rows)
        return parse_report(raw, expected_sha=hashlib.sha256(raw).hexdigest(), batch='123', expected_failed_items=items)

    def test_names_refer_to_later_sku_not_first(self):
        result = self.parse([
            ['1', '11', '普通,免安装', '100', '失败', '您的sku：定制;免安装 在管控期标价为30.00元；'],
            ['', '12', '定制,免安装', '50', '', '']])
        self.assertEqual(result['errors'][0]['sku'], '12')
        self.assertEqual(result['errors'][0]['submitted_price'], '50')
        self.assertEqual(result['sku_count'], 2)

    def test_coupon_discounted_cap_and_both_reasons(self):
        result = self.parse([['1', '11', '定制,免安装', '100', '失败',
            '[定制;免安装（活动普惠券后价：88.00元，最低普惠券后价：50.00元，9.0折折后券后价：45.00元）]。您的sku：定制;免安装 在管控期标价为60.00元；']])
        self.assertEqual([e['official_cap'] for e in result['errors']], ['45.00', '60.00'])

    def test_unknown_and_truncated_never_disappear(self):
        for message in ('未知资质错误', '您的sku：定制 在管控期标价为'):
            result = self.parse([['1', '11', '定制', '100', '失败', message]])
            self.assertEqual(result['errors'][0]['kind'], 'unknown')

    def test_ambiguous_names_no_guess(self):
        result = self.parse([['1', '11', '定制', '100', '失败', '您的sku：定制 在管控期标价为20.00元；'],
                             ['', '12', '定制', '100', '', '']])
        self.assertEqual(result['errors'][0]['sku'], '')

    def test_full_file_not_first_thousand_or_bad_dimension(self):
        rows = [[str(i), str(i+10000), '定制', '100', '失败', '未知错误'] for i in range(1, 1102)]
        result = self.parse(rows, tuple(str(i) for i in range(1, 1102)))
        self.assertEqual(result['item_count'], 1101)
        self.assertEqual(len(result['errors']), 1101)

    def test_scope_duplicate_and_success_block(self):
        for rows in ([['2', '11', '定制', '100', '失败', '未知']],
                     [['1', '11', '定制', '100', '成功', '未知']],
                     [['1', '11', '定制', '100', '失败', '未知'], ['', '11', '定制', '100', '', '']]):
            with self.assertRaises(ValueError):
                self.parse(rows)

    def test_file_binding_required(self):
        with self.assertRaises(ValueError):
            parse_report(package([]), expected_sha='wrong', batch='123', expected_failed_items=['1'])


if __name__ == '__main__':
    unittest.main()
