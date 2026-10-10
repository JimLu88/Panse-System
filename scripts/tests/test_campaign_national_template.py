"""Synthetic structural fixture; no production amounts, files or uploads."""
from copy import deepcopy
from io import BytesIO
from pathlib import Path
import sys
from xml.sax.saxutils import escape
from zipfile import ZipFile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import campaign_official_template as tpl
from test_campaign_draft52 import fixture


def national_fixture(state='未报名'):
    headers = {'A':'商品ID','D':'商品状态','E':'SKUID','M':'活动价',
               'N':'库存','O':'发货时间','P':'让利比例','Q':'补贴金额'}
    def row(n, values):
        return '<row r="'+str(n)+'">'+''.join(
            '<c r="'+c+str(n)+'" t="inlineStr"><is><t>'+escape(values.get(c, ''))+
            '</t></is></c>' for c in 'ABCDEFGHIJKLMNOPQ')+'</row>'
    data = row(1, {'A':'基础信息'})+row(2, headers)+row(3, {'P':'输入示例：10'})
    for n in range(4, 7):
        data += row(n, {'A':'1001358847694' if n==4 else '',
                       'D':state if n==4 else '', 'E':str(5991470332903+n)})
    merges = '<mergeCells count="3">'+''.join(
        '<mergeCell ref="'+c+'4:'+c+'6"/>' for c in ('A','D','P'))+'</mergeCells>'
    xml = '<worksheet xmlns="'+tpl.NS['s']+'"><sheetData>'+data+'</sheetData>'+merges+'</worksheet>'
    out = BytesIO()
    with ZipFile(BytesIO(fixture())) as source, ZipFile(out, 'w') as dest:
        for part in source.infolist():
            dest.writestr(part, xml.encode() if part.filename=='xl/worksheets/sheet1.xml' else source.read(part.filename))
    return out.getvalue()


def selected():
    return [dict(item=r['item'], sku=r['sku'], activity_price='100.00')
            for r in tpl.template_rows(national_fixture())]


def test_national_schema_reads_all_merged_children_without_rate_guess():
    raw = national_fixture()
    rows = tpl.template_rows(raw)
    assert len(rows)==3
    assert {r['item'] for r in rows}=={'1001358847694'}
    assert len({r['sku'] for r in rows})==3
    assert all(r['rate']=='' and r['state']=='未报名' for r in rows)
    assert tpl.template_requirements(raw)==dict(price_column='M',rate_column='P',
        amount_column='Q',explicit_subsidy_required=True)


@pytest.mark.parametrize('rate',['10%', '12%', '15%'])
def test_no_blank_required_subsidy_file_even_with_valid_explicit_rate(rate):
    with pytest.raises(ValueError, match='subsidy_amount_and_stacking_evidence_required'):
        tpl.fill_selected_rows(national_fixture(), selected(), official_rate=rate)


@pytest.mark.parametrize('amount',[None, '0', '10', '100.00'])
def test_unverified_amount_override_does_not_bypass_missing_business_contract(amount):
    rows = [dict(r,subsidy_amount=amount) for r in selected()]
    old = deepcopy(rows)
    issues = tpl.generation_input_issues(national_fixture(), rows)
    assert len(issues)==3
    assert rows==old
    assert all(i['error']=='official_subsidy_amount_and_stacking_evidence_required' for i in issues)


def test_no_issues_for_unchanged_legacy_layout_or_empty_signup_scope():
    assert tpl.generation_input_issues(fixture(), selected())==[]
    assert tpl.generation_input_issues(national_fixture(), [])==[]


def test_new_layout_still_rejects_renamed_required_column():
    headers={'A':'商品ID','D':'商品状态','E':'SKUID','M':'活动价','N':'库存',
             'O':'发货时间','P':'让利比例','Q':'最终到手价'}
    with pytest.raises(ValueError, match='columns_changed'):
        tpl._layout(headers)
