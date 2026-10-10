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


def alias_snapshot(tmp_path, root, row, *, sku='200', code='PPS001B1'):
    proof = tmp_path/'alias-proof.json'
    proof.write_text('{"saved":true}', encoding='utf-8')
    receipt = tmp_path/'alias-receipt.json'
    receipt.write_text(json.dumps(dict(status='verified_partial_mapping_restored', restored=[dict(
        item='101', sku=sku, erp_code='PPS001', official_sku_code=code,
        alias_evidence=[dict(path=str(proof), sha256=hashlib.sha256(proof.read_bytes()).hexdigest())])])), encoding='utf-8')
    bound = facts.bind_latest(snapshot([row]), root=root)
    bound['verified_code_alias_sources'] = [dict(path=str(receipt), sha256=hashlib.sha256(receipt.read_bytes()).hexdigest())]
    return bound, proof, receipt


def test_exact_verified_backup_alias_reused_by_generator(tmp_path):
    _, root, _ = fixture(tmp_path, code='PPS001B1', attributes='颜色定制')
    row = erp(sku='200', sku_name='颜色定制咨询')
    bound, _, _ = alias_snapshot(tmp_path, root, row)
    res = facts.FactResolver(bound)
    found, issue, evidence = res.resolve('101','200',[row])
    assert found == [row] and issue is None
    assert evidence['resolution'] == 'verified_code_alias' and evidence['alias_sources']
    a, d, i = build_rows(bound, [dict(item='101',sku='200',state='')], Decimal('.15'),'big',{})
    assert len(a)==1 and not d and not i and a[0]['erp_code']=='PPS001'


@pytest.mark.parametrize('part', ['proof', 'receipt'])
def test_alias_tamper_fails_closed(tmp_path, part):
    _, root, _ = fixture(tmp_path, code='PPS001B1')
    bound, proof, receipt = alias_snapshot(tmp_path, root, erp(sku='200'))
    (proof if part=='proof' else receipt).write_text('changed', encoding='utf-8')
    with pytest.raises(ValueError, match='changed'):
        facts.FactResolver(bound)


@pytest.mark.parametrize('alias_sku,alias_code', [('201','PPS001B1'),('200','PPS001B2')])
def test_alias_must_match_exact_pair_and_raw_code(tmp_path, alias_sku, alias_code):
    _, root, _ = fixture(tmp_path, code='PPS001B1')
    row = erp(sku='200')
    bound, _, _ = alias_snapshot(tmp_path, root, row, sku=alias_sku, code=alias_code)
    assert facts.FactResolver(bound).resolve('101','200',[row])[1]=='current_export_conflicts_with_erp_binding'


def test_alias_does_not_override_new_real_raw_code_owner(tmp_path):
    _, root, _ = fixture(tmp_path, code='PPS001B1')
    row = erp(sku='200')
    bound, _, _ = alias_snapshot(tmp_path, root, row)
    bound['all_erp_rows'].append(erp(code='PPS001B1',sku='201'))
    assert facts.FactResolver(bound).resolve('101','200',[row])[1]=='current_export_conflicts_with_erp_binding'


@pytest.mark.parametrize('bound_identity,same_spec,custom,allowed', [
    (True,True,False,True), (False,True,False,False),
    (True,False,False,False), (True,True,True,False)])
def test_numeric_legacy_requires_existing_ordinary_identity_and_exact_spec(tmp_path, bound_identity, same_spec, custom, allowed):
    attrs='适用人数:双人位2米;颜色分类:奶霜白;'
    _, root, _ = fixture(tmp_path, code='001', attributes=attrs)
    row=erp(sku='200',custom=custom,sku_name=attrs if same_spec else '双人位2.3米')
    res=facts.FactResolver(facts.bind_latest(snapshot([row]),root=root))
    found, issue, evidence=res.resolve('101','200',[row] if bound_identity else [])
    assert (issue is None)==allowed
    if allowed:
        assert found==[row] and evidence['resolution']=='verified_bound_legacy_prefix'


@pytest.mark.parametrize('attributes,name', [
    ('定制颜色（联系客服）,免安装','颜色定制'), ('其它定制','其它定制'),
    ('差价','差价'), ('其他样块','其他样块请咨询客服'),
    ('颜色定制','颜色定制咨询'), ('定制专拍','定制专拍'),
    ('红橡木(咨询客服)','红橡木(咨询客服)'), ('洞石背板','洞石背板'),
    ('追加配件联系客服','配件-竖隔板'), ('插座配件单拍咨询客服','插座配件单拍咨询客服'),
    ('定制咨询（联系客服）','其他定制')])
def test_existing_exact_custom_binding_does_not_require_keyword_whitelist(tmp_path, attributes, name):
    _,root,_=fixture(tmp_path,attributes=attributes)
    row=erp(sku='200',sku_name=name)
    res=facts.FactResolver(facts.bind_latest(snapshot([row]),root=root))
    assert res.resolve('101','200',[row])[:2]==([row],None)
    assert res.resolve('101','200',[])[1]=='custom_code_meaning_unverified'


@pytest.mark.parametrize('attributes', ['微调尺寸（联系客服）','微调尺寸（联系客服）,免安装'])
def test_micro_size_synonym(tmp_path, attributes):
    _,root,_=fixture(tmp_path,attributes=attributes)
    row=erp(sku='200',sku_name='尺寸微定制')
    res=facts.FactResolver(facts.bind_latest(snapshot([row]),root=root))
    assert res.resolve('101','200',[row])[:2]==([row],None)


def test_real_material_conflict_still_blocks_even_existing_binding(tmp_path):
    _,root,_=fixture(tmp_path,attributes='白色岩板定制咨询')
    row=erp(sku='200',sku_name='其他尺寸定制')
    res=facts.FactResolver(facts.bind_latest(snapshot([row]),root=root))
    assert res.resolve('101','200',[row])[1]=='merchant_code_semantic_conflict'


def test_multiple_existing_candidates_not_collapsed_by_code(tmp_path):
    _,root,_=fixture(tmp_path)
    row=erp(sku='200')
    res=facts.FactResolver(facts.bind_latest(snapshot([row]),root=root))
    assert res.resolve('101','200',[row,row])[1]=='current_export_conflicts_with_erp_binding'


def test_custom_conflict_not_a_discount_only_gate_but_signup_retains_it(tmp_path):
    _,root,_=fixture(tmp_path,attributes='白色岩板定制咨询')
    bound=facts.bind_latest(snapshot([erp(sku_name='其他尺寸定制'),
        erp(code='PPS002',sku='201',custom=False)]),root=root)
    ids=[dict(item='101',sku=s,state='') for s in ('200','201')]
    a,d,i=build_rows(bound,ids,Decimal('.1'),'medium',{},signup_items=set(),discount_items={'101'})
    assert not a and not i and len(d)==1 and d[0]['sku']=='201' and d[0]['deduct']=='90'
    a,d,i=build_rows(bound,ids,Decimal('.1'),'medium',{})
    assert len(i)==1 and i[0]['error']=='merchant_code_semantic_conflict'
    assert len(a)==len(d)==1


def test_mixed_custom_ordinary_conflict_not_skipped(tmp_path):
    _,root,_=fixture(tmp_path,code='PPS002')
    bound=facts.bind_latest(snapshot([erp(sku='200'),erp(code='PPS002',custom=False)]),root=root)
    a,d,i=build_rows(bound,[dict(item='101',sku='200',state='')],Decimal('.1'),'medium',{},signup_items=set())
    assert not a and not d and i[0]['error']=='current_export_conflicts_with_erp_binding'


@pytest.mark.parametrize('kind', ['blank','unknown_classification','duplicate_code','duplicate_binding'])
def test_uncertain_classification_cannot_be_skipped(tmp_path, kind):
    _,root,_=fixture(tmp_path,code='' if kind=='blank' else 'PPS001')
    row=erp(custom=None if kind=='unknown_classification' else True)
    rows=[row,row] if kind=='duplicate_code' else [row]
    res=facts.FactResolver(facts.bind_latest(snapshot(rows),root=root))
    existing=[row,row] if kind=='duplicate_binding' else []
    assert res.custom_only('101','200',existing) is False
