import hashlib
from io import BytesIO
import json
from pathlib import Path
import sys
import sqlite3
from xml.sax.saxutils import escape
from zipfile import ZipFile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import campaign_sku_fact_store as facts
from campaign_price_snapshot import digest
from campaign_generate_current_files import build_rows
from decimal import Decimal


def workbook(rows):
    cells = {3: {'A':'商品Id','D':'宝贝标题','G':'商家编码','J':'销售属性','L':'skuId','M':'价格(元)','N':'库存(件)','P':'商家编码'}}
    for n, row in enumerate(rows, 4):
        cells[n] = row
    xml = ''.join('<row r="%s">%s</row>' % (n, ''.join(
        '<c r="%s%s" t="inlineStr"><is><t>%s</t></is></c>' % (col, n, escape(value))
        for col, value in row.items())) for n, row in cells.items())
    stream = BytesIO()
    with ZipFile(stream, 'w') as z:
        z.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="发布模板" sheetId="1" r:id="r1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships><Relationship Id="r1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><dimension ref="A1"/><sheetData>'+xml+'</sheetData></worksheet>')
    return stream.getvalue()


def erp(**kw):
    return dict(dict(code='PPS001', item='100', product_item_id='100', product_alt_item_ids=['101'],
                     sku='900', alt=[], sku_name='榉木 尺寸微定制', custom=True, daily='500',
                     medium_target='360', big_target='330'), **kw)


def fixture(tmp_path, code='PPS001', item='101', attributes='颜色分类:榉木尺寸微定制;', rows=None):
    path = tmp_path / 'export.xlsx'
    path.write_bytes(workbook(rows or [{'A':item,'L':'200','G':'WRONG_PRODUCT_CODE','P':code,'J':attributes,'M':'999','N':'0'}]))
    root = tmp_path / 'cache'
    meta = facts.register(path, '2026-09-26T23:05:36+08:00', root=root)
    return path, root, meta


def snapshot(rows):
    return dict(all_erp_rows=rows, resolved_price_version_sha256=digest(rows))


def test_false_dimensions_and_raw_codes(tmp_path):
    _, root, meta = fixture(tmp_path, code=' PPS001 ')
    _, rows = facts.read_version(root=root)
    assert rows[0]['row'] == 4
    assert rows[0]['merchant_code'] == ' PPS001 '
    assert rows[0]['stock'] == '0'
    assert meta['coverage']['on_sale_scope_verified'] is False
    assert meta['exported_at'] != meta['updated_at']


def test_product_only_and_duplicates_preserved(tmp_path):
    rows = [{'A':'100','D':'no sku'}, {'A':'101','L':'200','P':'X'}, {'A':'101','L':'200','P':'Y'}]
    _, root, meta = fixture(tmp_path, rows=rows)
    _, parsed = facts.read_version(root=root)
    assert meta['counts'] == dict(products=2, data_rows=3, sku_rows=2, rows_without_sku_id=1, rows_with_blank_code=1)
    assert 'duplicate_item_sku' in parsed[1]['issues']
    resolver = facts.FactResolver(facts.bind_latest(snapshot([erp()]), root=root))
    assert resolver.resolve('101', '200', [erp()])[1] == 'duplicate_export_pair'


def test_idempotent_registration_and_time_conflict(tmp_path):
    path, root, first = fixture(tmp_path)
    assert facts.register(path, first['exported_at'], root=root) == first
    with pytest.raises(ValueError, match='time_conflict'):
        facts.register(path, '2026-09-27T23:05:36+08:00', root=root)


@pytest.mark.parametrize('part', ['source.xlsx', 'manifest.json'])
def test_tampering_rejected(tmp_path, part):
    _, root, meta = fixture(tmp_path)
    (root/'versions'/meta['source_sha256']/part).write_bytes(b'changed')
    with pytest.raises(ValueError, match='changed'):
        facts.read_version(root=root)


def test_newest_export_not_newest_registration(tmp_path):
    path, root, first = fixture(tmp_path)
    path.write_bytes(workbook([{'A':'300','L':'400','P':'OLD'}]))
    facts.register(path, '2026-09-25T23:05:36+08:00', root=root)
    assert facts.read_version(root=root)[0] == first


def test_same_time_different_files_is_ambiguous(tmp_path):
    path, root, _ = fixture(tmp_path)
    path.write_bytes(workbook([{'A':'300','L':'400','P':'OTHER'}]))
    facts.register(path, '2026-09-26T23:05:36+08:00', root=root)
    with pytest.raises(ValueError, match='ambiguous'):
        facts.read_version(root=root)


def test_index_date_tamper_rejected(tmp_path):
    _, root, _ = fixture(tmp_path)
    with sqlite3.connect(root/'index.sqlite3') as db:
        db.execute('UPDATE versions SET exported_epoch=exported_epoch+1')
    with pytest.raises(ValueError, match='index_metadata_changed'):
        facts.read_version(root=root)


def test_repeated_code_is_retained_and_flagged(tmp_path):
    _, root, _ = fixture(tmp_path, rows=[{'A':'101','L':'200','P':'X'},{'A':'101','L':'201','P':'X'}])
    _, rows = facts.read_version(root=root)
    assert len(rows) == 2
    assert all('repeated_merchant_code_in_product' in r['issues'] for r in rows)


def test_pinned_snapshot_stays_pinned(tmp_path):
    path, root, first = fixture(tmp_path)
    original = snapshot([erp()])
    bound = facts.bind_latest(original, root=root)
    assert 'sku_fact_source' not in original
    assert bound['all_erp_rows'] == original['all_erp_rows']
    assert bound['resolved_price_version_sha256'] == original['resolved_price_version_sha256']
    path.write_bytes(workbook([{'A':'300','L':'400','P':'NEW'}]))
    facts.register(path, '2026-09-27T23:05:36+08:00', root=root)
    assert facts.bind_latest(bound, root=root)['sku_fact_source']['sha256'] == first['source_sha256']


@pytest.mark.parametrize('attributes,name', [
    ('樱桃木其他尺寸定制咨询','白橡木/白蜡木定制'),
    ('黑胡桃木材质定制咨询','尺寸微定制'),
    ('白色岩板定制咨询','其他尺寸定制')])
def test_all_reported_custom_semantic_conflicts(tmp_path, attributes, name):
    _, root, _ = fixture(tmp_path, attributes=attributes)
    res = facts.FactResolver(facts.bind_latest(snapshot([erp(sku_name=name)]), root=root))
    assert res.resolve('101','200',[])[1] == 'merchant_code_semantic_conflict'


def test_no_cross_item_mapping(tmp_path):
    _, root, _ = fixture(tmp_path, item='999')
    res = facts.FactResolver(facts.bind_latest(snapshot([erp()]), root=root))
    assert res.resolve('999','200',[])[1] == 'merchant_code_product_alias_not_proven'


def test_empty_code_preserves_existing_but_cannot_create(tmp_path):
    _, root, _ = fixture(tmp_path, code='')
    row = erp()
    res = facts.FactResolver(facts.bind_latest(snapshot([row]), root=root))
    assert res.resolve('101','200',[row])[:2] == ([row],None)
    assert res.resolve('101','200',[])[1] == 'merchant_code_blank'


def test_code_cannot_overwrite_existing_binding(tmp_path):
    _, root, _ = fixture(tmp_path, code='DIFFERENT')
    row = erp()
    res = facts.FactResolver(facts.bind_latest(snapshot([row]), root=root))
    assert res.resolve('101','200',[row])[1] == 'current_export_conflicts_with_erp_binding'


def test_duplicate_erp_codes_blocked(tmp_path):
    _, root, _ = fixture(tmp_path)
    res = facts.FactResolver(facts.bind_latest(snapshot([erp(),erp()]), root=root))
    assert res.resolve('101','200',[])[1] == 'merchant_code_missing_or_ambiguous_in_erp'


def test_live_generator_consumes_precise_alias_without_changing_erp(tmp_path):
    _, root, meta = fixture(tmp_path)
    original = snapshot([erp()])
    bound = facts.bind_latest(original, root=root)
    activity, discounts, issues = build_rows(bound, [dict(item='101',sku='200',state='')], Decimal('.15'),'big',{})
    assert not issues and not discounts
    assert activity[0]['erp_code'] == 'PPS001'
    assert activity[0]['activity_price'] == '500'
    assert activity[0]['sku_fact_evidence']['source']['sha256'] == meta['source_sha256']
    assert original['all_erp_rows'][0]['sku'] == '900'
    assert original['all_erp_rows'][0]['alt'] == []
    # No Cartesian expansion of the new physical SKU to the current main link.
    _, _, issues = build_rows(bound, [dict(item='100',sku='200',state='')], Decimal('.15'),'big',{})
    assert issues[0]['error'] == 'erp_mapping_missing_or_not_unique'


def test_conflict_isolated_and_successful_not_replayed(tmp_path):
    _, root, _ = fixture(tmp_path, code='DIFFERENT')
    bound = facts.bind_latest(snapshot([erp()]), root=root)
    a,d,i = build_rows(bound,[dict(item='101',sku='200',state='活动中')], Decimal('.15'),'big',{},set(),set())
    assert (a,d,i) == ([],[],[])


def test_absent_registry_is_optional(tmp_path):
    original = snapshot([erp()])
    assert facts.bind_latest(original, root=tmp_path/'missing') == original


@pytest.mark.parametrize('time', ['2026-09-26T23:05:36', 'bad'])
def test_explicit_timezone_required(time):
    with pytest.raises(ValueError):
        facts.timestamp(time)
