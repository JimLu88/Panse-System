import ast
from pathlib import Path
import sys
import json
import sqlite3
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from campaign_final_audit import reconcile, manifest, finalize, POLICY, parse_inventory

WINDOW={'start':'2026-09-14 00:00:00','end':'2026-09-16 19:59:59'}
EXPECTED=[{'item':'719436834260','sku':'100000001'},{'item':'719436834260','sku':'100000002'}]

def check(rows, **kw):
    return reconcile(EXPECTED, rows,identity={'campaign_id':'legacy'},window=WINDOW,
                     observed_at=kw.pop('observed_at','2026-09-14T02:00:00+00:00'),
                     coverage_complete=kw.pop('coverage_complete',True),**kw)

def row(sku='100000001',state='已发布设定',**kw):
    return dict(item='719436834260',sku=sku,state=state,marketing_id='123',row=4,activity_price='30',**kw)

def test_one_registered_sku_is_partial_not_all():
    r=check([row()]);assert not r['all_registered']
    assert r['products'][0]['status']=='sku_scope_unconfirmed'
    assert r['rows'][1]['status']=='not_in_current_export'

def test_future_registered_is_not_effective():
    r=check([row(),row('100000002')],observed_at='2026-09-13T02:00:00+00:00')
    assert r['all_registered'] and not r['all_currently_effective']
    assert r['counts']=={'registered_pending_window':2}


def test_segment_price_window_never_falls_back_to_longterm_activity(monkeypatch):
    from campaign_final_audit import target_window
    import campaign_segmented_time
    binding={'request_path':'fixture','segment':{'segment_id':'a','price_window':WINDOW,
              'official_window':{'start':'2025-06-21 00:00:00','end':'2028-07-31 23:59:59'}}}
    monkeypatch.setattr(campaign_segmented_time,'bind_request',lambda p,s:binding)
    assert target_window({'identity':binding['segment']['official_window'],'time_binding':binding})==WINDOW

def test_published_is_not_currently_effective():
    assert not check([row(),row('100000002')])['all_currently_effective']

def test_current_effective_requires_all_skus():
    assert check([row(state='活动中'),row('100000002',state='活动中')])['all_currently_effective']

def test_stale_or_partial_coverage_cannot_call_missing_failure():
    r=check([row()],coverage_complete=False)
    assert r['counts']=={'unknown_coverage':2} and not r['all_registered']

@pytest.mark.parametrize('state',['异常','草稿','暂停','撤销报名','未认识的新状态'])
def test_non_accepted_states_never_success(state):
    r=check([row(state=state),row('100000002',state=state)])
    assert not r['all_registered']

def test_cancelled_old_record_does_not_hide_new_registration():
    rows=[row(state='撤销报名'),row(),row('100000002')]
    assert check(rows)['all_registered']

def test_conflicting_live_marketing_records_remain_ambiguous():
    r=check([row(),row(),row('100000002')]);assert not r['all_registered']
    assert r['rows'][0]['status']=='ambiguous_marketing_records'

def test_blank_sku_price_not_inherited_from_other_variant():
    missing=row('100000002');missing['activity_price']=''
    assert check([row(),missing])['rows'][1]['status']=='sku_price_missing'

def test_wrong_product_same_sku_does_not_match():
    wrong=row();wrong['item']='111111111111'
    assert check([wrong])['rows'][0]['status']=='not_in_current_export'

def test_expired_is_not_effective():
    assert not check([row(state='活动中'),row('100000002',state='活动中')],observed_at='2026-09-17T00:00:00+00:00')['all_currently_effective']

def test_no_scope_still_creates_durable_negative_audit(tmp_path):
    result={'status':'complete','all_signed_up':True,'segments':[]}
    final=finalize({'schema':'continuous_campaign_residual_v1'},result,root=tmp_path,authority=None,edge=None,artifact_roots=[])
    assert not final['all_signed_up'] and not final['final_audit']['verified']
    assert Path(final['final_audit']['evidence']).exists()
    assert finalize({'schema':'continuous_campaign_residual_v1'},result,root=tmp_path,authority=None,edge=None,artifact_roots=[])==final

def test_every_cli_execution_branch_uses_finish():
    source=(Path(__file__).resolve().parents[1]/'scripts/campaign_continuous_execute.py').read_text(encoding='utf-8')
    tree=ast.parse(source)
    renders=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)
             and n.func.attr=='dumps' and n.args and isinstance(n.args[0],ast.Call)
             and isinstance(n.args[0].func,ast.Name) and n.args[0].func.id=='finish']
    assert len(renders)==4

def test_parser_merged_product_rows(tmp_path):
    import openpyxl
    from io import BytesIO
    wb=openpyxl.Workbook();ws=wb.active;ws.title='已报商品列表'
    ws.append(['商品ID','SKUID','商品状态','营销ID','活动价'])
    ws.append(['719436834260','100000001','已发布设定','123',30])
    ws.append([None,'100000002',None,None,None])
    stream=BytesIO();wb.save(stream)
    rows=parse_inventory(stream.getvalue())
    assert rows[1]['item']=='719436834260'
    assert rows[1]['activity_price']==''
    assert not check(rows)['all_registered']


def test_repaired_read_reuses_only_same_execution_pinned_audit(tmp_path):
    from campaign_final_audit import reusable_segments
    from campaign_continuous_policy import fingerprint
    from campaign_entry_authority import file_sha
    raw=dict(status='complete',segments=[{'segment_id':'s'}])
    path=tmp_path/'final-audit'/fingerprint([POLICY,3,raw])/'audit-v3.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'segments':[{'segment_id':'s','all_registered':True}]}))
    result=dict(raw,final_audit=dict(evidence=str(path),sha256=file_sha(path)))
    assert reusable_segments(tmp_path,result,raw)['s']['all_registered'] is True
    assert reusable_segments(tmp_path,result,dict(raw,status='blocked'))=={}
    path.write_text('{}')
    with pytest.raises(ValueError,match='receipt_changed'):reusable_segments(tmp_path,result,raw)


def test_successful_sibling_export_not_requested_again(tmp_path,monkeypatch):
    import campaign_final_audit as mod
    from campaign_entry_authority import file_sha
    from campaign_continuous_policy import fingerprint
    raw=dict(status='complete',segments=[{'segment_id':'s','success':{EXPECTED[0]['item']:'receipt'}}])
    source=tmp_path/'export.xlsx';source.write_bytes(b'unchanged')
    old=check([row(),row('100000002')])
    old.update(segment_id='s',scope_sha256='scope',source_file={'path':str(source),'sha256':file_sha(source)})
    path=tmp_path/'final-audit'/fingerprint([POLICY,3,raw])/'audit-v3.json'
    path.parent.mkdir(parents=True);path.write_text(json.dumps({'segments':[old]}))
    result=dict(raw,final_audit=dict(evidence=str(path),sha256=file_sha(path)))
    monkeypatch.setattr(mod,'manifest',lambda *a:dict(scope_sha256='scope',window=WINDOW,pairs=EXPECTED))
    def no_transport(*a,**kw):raise AssertionError('successful export repeated')
    monkeypatch.setattr(mod,'CampaignTransport',no_transport)
    request=dict(schema='fixture',pages={'legacy':{'campaign_id':'legacy'}})
    final=mod.finalize(request,result,root=tmp_path,authority=None,edge=None,artifact_roots=[])
    assert final['all_signed_up'] is True


def test_registered_export_does_not_hide_missing_discount(tmp_path,monkeypatch):
    import campaign_final_audit as mod
    from campaign_entry_authority import file_sha
    from campaign_continuous_policy import fingerprint
    raw=dict(status='complete',segments=[{'segment_id':'s'}])
    source=tmp_path/'export.xlsx';source.write_bytes(b'unchanged')
    old=check([row(),row('100000002')]);old.update(segment_id='s',scope_sha256='scope',
        source_file={'path':str(source),'sha256':file_sha(source)})
    path=tmp_path/'final-audit'/fingerprint([POLICY,3,raw])/'audit-v3.json'
    path.parent.mkdir(parents=True);path.write_text(json.dumps({'segments':[old]}))
    result=dict(raw,final_audit=dict(evidence=str(path),sha256=file_sha(path)))
    gap=dict(reason='protected_registration_new_sku_discount_unverified',item=EXPECTED[0]['item'])
    monkeypatch.setattr(mod,'manifest',lambda *a:dict(scope_sha256='scope',window=WINDOW,pairs=EXPECTED,protected_discount_gaps=[gap]))
    final=mod.finalize(dict(schema='fixture',pages={'l':{'campaign_id':'legacy'}}),result,
        root=tmp_path,authority=None,edge=None,artifact_roots=[])
    assert final['all_signed_up'] is False
    assert final['final_audit']['segments'][0]['all_registered'] is True
    assert final['final_audit']['gaps'][0]['reason']==gap['reason']
