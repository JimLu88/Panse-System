import copy
from io import BytesIO
from pathlib import Path
import sys
from xml.sax.saxutils import escape
from zipfile import ZipFile

import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import campaign_official_current as c


def row(**kw):
    return dict(dict(item='10001',name='产品',marketing_id='20001',state='异常',sku='30001',
                spec='规格',final='21.01',cap='21.01',activity_price='30',excel_row=4),**kw)


def package(merged=True,blank_unmerged=False):
    rows={2:c.FIELDS,3:dict(I='券后价需要低于或等于此价格',K='仅已报名商品可见当前券后价',C='导入不识别营销ID',D='导入不识别状态'),
          4:dict(A='10001',B='产品',C='20001',D='异常',E='30001',F='甲',I='21.01',K='21.01',P='30'),
          5:dict(E='30002',F='乙',I='21.01',K='21.00',P='30')}
    if not merged and not blank_unmerged:rows[5].update(A='10001',B='产品',C='20001',D='异常')
    cells=''.join('<row r="'+str(n)+'">'+''.join('<c r="'+col+str(n)+'" t="inlineStr"><is><t>'+escape(str(v))+'</t></is></c>' for col,v in r.items())+'</row>' for n,r in rows.items())
    merges='<mergeCells>'+''.join('<mergeCell ref="'+col+'4:'+col+'5"/>' for col in 'ABCD')+'</mergeCells>' if merged else ''
    data=BytesIO()
    with ZipFile(data,'w') as z:
        z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="已报商品列表" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr('xl/worksheets/sheet1.xml','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><dimension ref="A1"/><sheetData>'+cells+'</sheetData>'+merges+'</worksheet>')
    return data.getvalue()


@pytest.mark.parametrize('merged',[True,False])
def test_physical_rows_survive_bad_dimension_and_merged_identity(merged):
    rows,instructions=c.parse(package(merged))
    assert len(rows)==2 and rows[1]['item']=='10001' and rows[1]['marketing_id']=='20001'
    assert rows[1]['state']=='异常' and rows[1]['sku']=='30002'
    assert instructions['abnormal_recovery_instruction'] is None


def test_blank_identity_without_real_merge_is_not_forward_filled():
    with pytest.raises(ValueError,match='not_merged'):c.parse(package(False,True))


@pytest.mark.parametrize('final,cap,status',[
    ('21.01','21.01','价格满足'),('21.00','21.01','价格满足'),('21.02','21.01','券后价超限'),
    (None,'21.01','价格证据缺失'),('21.01',None,'价格证据缺失'),('bad','21.01','价格证据无效'),
    ('0','0','价格满足'),
])
def test_price_evidence_not_state_or_missing_zero(final,cap,status):
    assert c.price_status(row(final=final,cap=cap))==status


def test_withdrawn_history_draft_and_current_do_not_mix():
    rows=[row(),row(marketing_id='19999',state='撤销报名',final='999'),
          row(item='10002',marketing_id='20002',state='草稿',sku='30003')]
    r=c.audit_rows(rows,{('10001','30001')})
    assert r['current_items']==1 and r['current_skus']==1 and r['draft_records']==1
    assert r['matched_scope']['price_counts']=={'价格满足':1}
    assert r['matched_scope']['record_states']=={'异常':1}
    assert not r['enrollment_success_claimed'] and not r['whole_event_complete'] and not r['upload_ready']
    assert r['recovery_path_status']=='not_proven_from_export'


def test_satisfied_one_sku_does_not_prove_whole_product():
    r=c.audit_rows([row(),row(sku='30002',final='99')],{('10001','30001')})
    assert not r['products'][0]['all_sku_price_satisfied'] and len(r['overcap_rows'])==1
    assert r['protected_price_satisfied_pairs']==[['10001','30001']]


def test_unknown_current_state_is_not_reclassified_as_success():
    r=c.audit_rows([row(state='未知')],{('10001','30001')})
    assert r['matched_scope']['missing']==[('10001','30001')] and not r['protected_active_pairs']


@pytest.mark.parametrize('kind',['sku','marketing','state'])
def test_ambiguous_records_fail_closed(kind):
    peer=row()
    if kind=='marketing':peer.update(marketing_id='20002',sku='30002')
    if kind=='state':peer.update(state='活动中',sku='30002')
    with pytest.raises(ValueError):c.audit_rows([row(),peer],{('10001','30001')})


def test_active_state_is_separate_protection_even_with_unknown_price():
    r=c.audit_rows([row(state='活动中',final=None)],{('10001','30001')})
    assert r['protected_active_pairs']==[['10001','30001']] and not r['protected_price_satisfied_pairs']


def test_old_prepare_cannot_regenerate_satisfied_scope(tmp_path,monkeypatch):
    import campaign_cap_prepare as old
    monkeypatch.setattr(c,'current_audit',lambda:dict(confirmed=True))
    with pytest.raises(ValueError,match='supersedes_old_prepare'):
        old.prepare('2026-09-28 03:00:00',tmp_path/'must_not_exist')
    assert not (tmp_path/'must_not_exist').exists()


def test_tampered_latest_source_cannot_fall_back_to_old_data(tmp_path):
    p=tmp_path/'input';p.write_bytes(b'changed')
    with pytest.raises(ValueError,match='source_changed'):c.pinned(p,'0'*64)
