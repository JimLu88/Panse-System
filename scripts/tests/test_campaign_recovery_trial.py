from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import sys
from xml.sax.saxutils import escape
from zipfile import ZipFile

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import campaign_recovery_trial as c
from campaign_official_current import FIELDS
from campaign_official_template import fill_selected_rows
from test_campaign_current_rate import super_fixture

ITEM='917179577721'
MARKETING='10030744195531'
SKUS=[str(6241018727153+n) for n in range(4,8)]
SCOPE=[dict(item=ITEM,sku=s,activity_price='30.00') for s in SKUS]


def package(*, terminal=False, state='异常', status='成功', final='21.01', cap='21.01',
            marketing=MARKETING, rate='10', merged=True, duplicate=False):
    headers = dict(FIELDS,S='让利比例',T='补贴金额')
    if terminal:
        headers=dict(A='商品ID',E='SKUID',N='活动价',X='让利比例',Y='补贴金额',Z='是否成功',AA='失败原因或风险提示')
    cells={1:{'X' if terminal else 'S':'超级立减(15144972)(必填)'},2:headers,3:dict(A='说明')}
    for n,sku in enumerate(SKUS,4):
        r=dict(E=sku,F='规格',I=cap,K=final,P='30.00',S=rate,T='')
        if terminal:
            r=dict(E=sku,N='30.00',X=rate,Y='',K='旧参考999',D='异常',Z=status,AA='风险提示：最低标价风险')
        if not merged or n==4:
            r.update(A=ITEM,B='测试商品',C=marketing,D=state)
        elif terminal:
            r.pop('Z');r.pop('AA')
        cells[n]=r
    if duplicate:cells[8]=dict(cells[4])
    data=''.join('<row r="'+str(n)+'">'+''.join('<c r="'+k+str(n)+'" t="inlineStr"><is><t>'+escape(str(v))+'</t></is></c>' for k,v in row.items() if v is not None)+'</row>' for n,row in cells.items())
    columns=['A','B','C','D']+(['Z','AA'] if terminal else [])
    merges='<mergeCells>'+''.join('<mergeCell ref="'+k+'4:'+k+'7"/>' for k in columns)+'</mergeCells>' if merged else ''
    b=BytesIO()
    with ZipFile(b,'w') as z:
        name='商品SKU导入列表' if terminal else '已报商品列表'
        z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="'+name+'" r:id="r1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<Relationships><Relationship Id="r1" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr('xl/worksheets/sheet1.xml','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><dimension ref="A1"/><sheetData>'+data+'</sheetData>'+merges+'</worksheet>')
    return b.getvalue()


def request():
    return dict(schema='campaign_recovery_trial_request_v1',experiment_type=c.RECOVERY,
                template_kind='super_reduce_activity',campaign=c.CAMPAIGN,item=ITEM,
                original_marketing_id=MARKETING,scope=deepcopy(SCOPE),official_rate='10%',
                platform_write=False,price_change=False,withdraw=False,single_discount_change=False,
                automatic_expand=False)


def authorization(r):
    return dict(r,user_authorization='test-only current exact user authority',
                prices_changed=False,single_discount_changed=False)


def save(tmp, name, value):
    path=tmp/name
    path.write_bytes(value if isinstance(value,bytes) else json.dumps(value).encode())
    return dict(path=str(path),sha256=c.sha(path.read_bytes()))


@pytest.fixture
def local(tmp_path,monkeypatch):
    r=request();master=super_fixture()
    monkeypatch.setattr(c,'MASTER_SHA',c.sha(master))
    r.update(authorization=save(tmp_path,'auth.json',authorization(r)),
             source=save(tmp_path,'source.xlsx',package()),master=save(tmp_path,'master.xlsx',master))
    req=save(tmp_path,'request.json',r)
    prepared=c.prepare(req['path'],output_dir=tmp_path/'output',ledger=tmp_path/'ledger')
    return r,req,prepared


def test_merged_complete_scope_survives_false_dimension():
    r=request()
    assert len(c.validate(r,authorization(r),package()))==4


@pytest.mark.parametrize('state',['活动中','已发布设定','进行中','已生效','未知','草稿'])
def test_never_trial_active_unknown_or_draft(state):
    r=request()
    with pytest.raises(ValueError,match='must_not_replay'):
        c.validate(r,authorization(r),package(state=state))


@pytest.mark.parametrize('final,cap',[('22','21.01'),(None,'21.01'),('21.01',None)])
def test_price_failure_is_not_recovery(final,cap):
    r=request()
    with pytest.raises(ValueError,match='not_price_satisfied'):
        c.validate(r,authorization(r),package(final=final,cap=cap))


@pytest.mark.parametrize('field,value',[('official_rate','15%'),('price_change',True),
    ('withdraw',True),('platform_write',True),('single_discount_change',True),('automatic_expand',True),
    ('campaign','49646/49651/3555037899'),('template_kind','single_discount')])
def test_wrong_intent_or_expansion_rejected(field,value):
    r=request();r[field]=value
    with pytest.raises(ValueError):c.validate(r,authorization(r),package())


def test_single_discount_routes_separately():
    assert c.route(c.DISCOUNT,'single_discount')==c.DISCOUNT
    r=request();r.update(experiment_type=c.DISCOUNT,template_kind='single_discount')
    with pytest.raises(ValueError,match='not_recovery'):c.validate(r,authorization(r),package())


@pytest.mark.parametrize('change',['partial','price','marketing','duplicate','noauth'])
def test_exact_scope_authorization_binding(change):
    r=request();a=authorization(r);raw=package()
    if change=='partial':r['scope'].pop();a=authorization(r)
    if change=='price':r['scope'][0]['activity_price']='30.01';a=authorization(r)
    if change=='marketing':raw=package(marketing='10030744195532')
    if change=='duplicate':raw=package(duplicate=True)
    if change=='noauth':a['user_authorization']=''
    with pytest.raises(ValueError):c.validate(r,a,raw)


def test_discount_success_does_not_override_explicit_activity_trial():
    r=request();r['single_discount_status']='success'
    assert len(c.validate(r,authorization(r),package()))==4
    a=authorization(r);a['experiment_type']=c.DISCOUNT
    with pytest.raises(ValueError):c.validate(r,a,package())


def test_prepare_is_persistent_no_replay_other_output(local,tmp_path):
    _,req,p=local
    assert p['state']=='prepared_not_submitted' and p['platform_write'] is False
    with pytest.raises(ValueError,match='already_prepared'):
        c.prepare(req['path'],output_dir=tmp_path/'other',ledger=tmp_path/'ledger')
    assert not (tmp_path/'other').exists()


def test_adopt_same_file_and_package_preservation(local,tmp_path):
    r,req,p=local
    adopted=c.prepare(req['path'],workbook_path=p['file'],ledger=tmp_path/'adopt-ledger')
    assert adopted['file']==p['file'] and adopted['file_sha256']==p['file_sha256']
    with ZipFile(r['master']['path']) as src,ZipFile(p['file']) as out:
        assert src.namelist()==out.namelist()
        assert all(src.read(n)==out.read(n) for n in src.namelist() if n!='xl/worksheets/sheet1.xml')


def event(tmp,p,kind,raw=None,**more):
    return dict(experiment_type=c.RECOVERY,campaign=c.CAMPAIGN,item=ITEM,file_sha256=p['file_sha256'],
                kind=kind,source=save(tmp,kind+'.evidence',raw or b'local test observation'),**more)


@pytest.mark.parametrize('kind',['risk_warning','export_refresh'])
def test_ui_warning_and_export_refresh_are_not_terminal(local,tmp_path,kind):
    _,_,p=local
    assert c.transition(p,p,event(tmp_path,p,kind))==p


def test_official_merged_success_ignores_reference_state_price_and_risk_warning(local,tmp_path):
    r,_,p=local
    parsed=c.import_terminal(r,package(terminal=True))
    assert parsed['status']=='success' and len(parsed['rows'])==4
    assert all(x['status']=='成功' and x['warning'] for x in parsed['rows'])
    next_state=c.transition(p,p,event(tmp_path,p,'official_import_terminal',package(terminal=True),operation_reference='local-test-import-1'))
    assert next_state['state']=='awaiting_same_marketing_readback' and not next_state['business_complete']


def test_export_cannot_be_passed_as_official_import_terminal(local,tmp_path):
    _,_,p=local
    with pytest.raises(ValueError):
        c.transition(p,p,event(tmp_path,p,'official_import_terminal',package(),operation_reference='test'))


@pytest.mark.parametrize('status,expected',[('失败','failed_no_retry'),('','unknown_no_retry')])
def test_failed_unknown_no_resubmit(local,tmp_path,status,expected):
    _,_,p=local
    s=c.transition(p,p,event(tmp_path,p,'official_import_terminal',package(terminal=True,status=status),operation_reference='test'))
    assert s['state']==expected and not s['automatic_retry']
    with pytest.raises(ValueError):c.transition(p,s,event(tmp_path,p,'user_uploaded'))


def test_real_readback_required_and_does_not_expand(local,tmp_path):
    _,_,p=local
    ev=event(tmp_path,p,'official_import_terminal',package(terminal=True),operation_reference='test')
    s=c.transition(p,p,ev)
    rb=event(tmp_path,p,'same_marketing_readback',package(),operation_reference='test')
    assert c.transition(p,s,rb)['state']=='not_recovered_no_retry'
    rb=event(tmp_path,p,'same_marketing_readback',package(state='活动中'),operation_reference='test')
    done=c.transition(p,s,rb)
    assert done['state']=='recovered_this_trial' and done['business_complete'] and not done['automatic_expand']
    assert c.transition(p,done,ev)['state']=='recovered_this_trial'


def test_wrong_marketing_and_duplicate_readback_never_success(local,tmp_path):
    _,_,p=local
    s=c.transition(p,p,event(tmp_path,p,'official_import_terminal',package(terminal=True),operation_reference='test'))
    for raw in [package(state='活动中',marketing='10000000001'),package(state='活动中',duplicate=True)]:
        with pytest.raises(ValueError):c.transition(p,s,event(tmp_path,p,'same_marketing_readback',raw,operation_reference='test'))


def test_tampered_source_or_master_fails_before_output(local,tmp_path):
    r,req,p=local
    Path(r['source']['path']).write_bytes(b'tampered')
    with pytest.raises(ValueError,match='hash_changed'):
        c.prepare(req['path'],output_dir=tmp_path/'no',ledger=tmp_path/'new-ledger')
    assert not (tmp_path/'no').exists()


def test_record_appends_with_no_external_writes(local,tmp_path):
    _,_,p=local
    e=event(tmp_path,p,'official_import_terminal',package(terminal=True),operation_reference='test')
    f=save(tmp_path,'event.json',e)
    result=c.record(p['trial_dir'],f['path'])
    assert result['state']=='awaiting_same_marketing_readback'
    assert (Path(p['trial_dir'])/'event-0001.json').exists()
    assert not result['platform_write'] and not result['automatic_retry']


def test_changed_workbook_cannot_record_result(local,tmp_path):
    _,_,p=local
    Path(p['file']).write_bytes(b'wrong upload file')
    e=save(tmp_path,'event.json',{})
    with pytest.raises(ValueError,match='workbook_changed'):c.record(p['trial_dir'],e['path'])


def test_explicit_batch_scope_is_one_request_not_auto_expansion(local,tmp_path):
    r,_,p=local
    batch=deepcopy(r)
    batch['marketing_records']={ITEM:MARKETING}
    batch.pop('item');batch.pop('original_marketing_id')
    batch['authorization']=save(tmp_path,'batch-auth.json',authorization(batch))
    assert len(c.build(batch)[1])==4
    req=save(tmp_path,'batch-request.json',batch)
    # Changing from single-item to a new batch name cannot replay a prepared scope.
    with pytest.raises(ValueError,match='already_prepared'):
        c.prepare(req['path'],output_dir=tmp_path/'batch-output',ledger=tmp_path/'ledger')


def test_batch_cannot_invent_or_silently_omit_other_product():
    r=request();r['marketing_records']={ITEM:MARKETING,'123456789012':'123456789013'}
    r.pop('item');r.pop('original_marketing_id')
    with pytest.raises(ValueError,match='scope_missing'):c.validate(r,authorization(r),package())


def test_batch_scope_needs_its_own_matching_user_authorization():
    r=request();auth=authorization(r)
    r['marketing_records']={ITEM:MARKETING};r.pop('item');r.pop('original_marketing_id')
    with pytest.raises(ValueError,match='authorization_scope_mismatch'):c.validate(r,auth,package())


def test_official_success_with_warning_is_not_failure_even_after_upload(local,tmp_path):
    _,_,p=local
    uploaded=c.transition(p,p,event(tmp_path,p,'user_uploaded'))
    e=event(tmp_path,p,'official_import_terminal',package(terminal=True),operation_reference='test',status='failed')
    with pytest.raises(ValueError,match='disagrees'):c.transition(p,uploaded,e)


def test_terminal_cannot_use_wrong_event_object(local,tmp_path):
    _,_,p=local
    e=event(tmp_path,p,'official_import_terminal',package(terminal=True),operation_reference='test')
    e['experiment_type']=c.DISCOUNT
    with pytest.raises(ValueError,match='mismatch'):c.transition(p,p,e)


def test_mixed_official_result_preserves_success_and_no_sales(local,tmp_path):
    r,_,p=local
    raw=package(terminal=True,merged=False)
    buf=BytesIO()
    with ZipFile(BytesIO(raw)) as src,ZipFile(buf,'w') as dest:
        for part in src.infolist():
            data=src.read(part.filename)
            if part.filename=='xl/worksheets/sheet1.xml':
                text=data.decode().replace('r="Z4" t="inlineStr"><is><t>成功','r="Z4" t="inlineStr"><is><t>失败')
                text=text.replace('r="AA4" t="inlineStr"><is><t>风险提示：最低标价风险',
                                  'r="AA4" t="inlineStr"><is><t>您的资质近60天销售件数0件，活动要求大于等于1')
                data=text.encode()
            dest.writestr(part,data)
    parsed=c.import_terminal(r,buf.getvalue())
    assert parsed['status']=='partial' and len(parsed['successful_pairs'])==3 and len(parsed['failed_pairs'])==1
    assert parsed['no_sales_items']==[ITEM] and parsed['no_sales_applies_to']=='this_campaign_only_not_global_blacklist'
    s=c.transition(p,p,event(tmp_path,p,'official_import_terminal',buf.getvalue(),operation_reference='test'))
    assert s['state']=='partially_accepted_no_retry' and not s['business_complete']


def test_batch_delivery_adapter_requires_all_protection_flags():
    d=dict(operation=c.RECOVERY,authorization='exact user batch request',items=[dict(item=ITEM,marketing_id=MARKETING)],
           rows=SCOPE,rate='10%',activity_prices_unchanged=True,excluded_single_discount_upload=True,
           single_discount106_untouched=True,platform_write=False)
    normalized=c.normalize_authorization(d)
    assert normalized['marketing_records']=={ITEM:MARKETING} and normalized['scope']==SCOPE
    for key in ('activity_prices_unchanged','excluded_single_discount_upload','single_discount106_untouched'):
        bad=dict(d);bad[key]=False
        with pytest.raises(ValueError):c.normalize_authorization(bad)


@pytest.mark.parametrize('batch',[False,True])
def test_adopt_delivery_consumes_existing_file_without_regeneration(local,tmp_path,monkeypatch,batch):
    r,_,p=local
    monkeypatch.setattr(c,'MASTER',Path(r['master']['path']))
    audit=save(tmp_path,'audit.json',dict(source=r['source']))
    if batch:
        doc=dict(operation=c.RECOVERY,authorization='explicit batch test authorization',
                 items=[dict(item=ITEM,marketing_id=MARKETING)],rows=SCOPE,rate='10%',
                 activity_prices_unchanged=True,excluded_single_discount_upload=True,
                 single_discount106_untouched=True,platform_write=False,
                 current_source=audit['path'],current_source_sha256=audit['sha256'])
    else:
        doc=dict(authorization(r),source=audit['path'],source_sha256=audit['sha256'])
    doc.update(file=p['file'],sha256=p['file_sha256'])
    receipt=save(tmp_path,'delivery.json',doc)
    before=set(tmp_path.rglob('*.xlsx'))
    result=c.adopt_delivery(receipt['path'],ledger=tmp_path/'delivery-ledger')
    assert result['file_sha256']==p['file_sha256'] and not result['platform_write']
    assert set(tmp_path.rglob('*.xlsx'))==before
    assert len(c.records(result['request']))==1
    with pytest.raises(ValueError,match='already_prepared'):
        c.adopt_delivery(receipt['path'],ledger=tmp_path/'delivery-ledger')


def test_missing_original_delivery_is_not_regenerated(local,tmp_path,monkeypatch):
    r,_,p=local
    monkeypatch.setattr(c,'MASTER',Path(r['master']['path']))
    audit=save(tmp_path,'audit.json',dict(source=r['source']))
    doc=dict(authorization(r),source=audit['path'],source_sha256=audit['sha256'],
             file=str(tmp_path/'missing.xlsx'),sha256=p['file_sha256'])
    receipt=save(tmp_path,'delivery.json',doc)
    with pytest.raises(FileNotFoundError):
        c.adopt_delivery(receipt['path'],ledger=tmp_path/'delivery-ledger')
    assert not (tmp_path/'missing.xlsx').exists()
    assert not (tmp_path/'delivery-ledger').exists()


@pytest.mark.parametrize('status,expected',[('成功','awaiting_same_marketing_readback'),
    ('失败','failed_no_retry'),('','unknown_no_retry')])
def test_historical_result_missing_original_holds_scope_no_regeneration(local,tmp_path,status,expected):
    r,req,p=local
    audit=save(tmp_path,'audit.json',dict(source=r['source']))
    doc=dict(authorization(r),source=audit['path'],source_sha256=audit['sha256'],
             file=str(tmp_path/'missing.xlsx'),sha256=p['file_sha256'])
    receipt=save(tmp_path,'delivery.json',doc)
    official=save(tmp_path,'official.xlsx',package(terminal=True,status=status))
    before=set(tmp_path.rglob('*.xlsx'))
    result=c.register_result(receipt['path'],official['path'],official['sha256'],'test import',ledger=tmp_path/'history')
    assert result['state']==expected and result['original_workbook_missing']
    assert not result['original_workbook_verified'] and not result['business_complete']
    assert set(tmp_path.rglob('*.xlsx'))==before
    with pytest.raises(ValueError,match='already_prepared'):
        c.prepare(req['path'],output_dir=tmp_path/'forbidden',ledger=tmp_path/'history')
    if status=='成功':
        e=event(tmp_path,result,'same_marketing_readback',package(state='活动中'),operation_reference='test import')
        efile=save(tmp_path,'readback.json',e)
        rb=c.record(result['trial_dir'],efile['path'])
        assert rb['state']=='recovered_this_trial' and rb['original_workbook_missing']


@pytest.mark.parametrize('bad',['hash','scope','price','missing_operation','original_hash'])
def test_historical_registration_requires_real_matching_report(local,tmp_path,bad):
    r,_,p=local
    audit=save(tmp_path,'audit.json',dict(source=r['source']))
    doc=dict(authorization(r),source=audit['path'],source_sha256=audit['sha256'],
             file=str(tmp_path/'missing.xlsx'),sha256=p['file_sha256'])
    raw=package(terminal=True)
    if bad=='scope': doc['scope']=doc['scope'][:-1]
    if bad=='price': doc['scope'][0]['activity_price']='31.00'
    if bad=='original_hash': doc['sha256']=''
    receipt=save(tmp_path,'delivery.json',doc)
    official=save(tmp_path,'official.xlsx',raw)
    with pytest.raises(ValueError):
        c.register_result(receipt['path'],official['path'],'0'*64 if bad=='hash' else official['sha256'],
                          '' if bad=='missing_operation' else 'test import',ledger=tmp_path/'history')
    assert not (tmp_path/'history').exists()
