import copy
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys

import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import campaign_cap_prepare as c

I,S='12345678901','23456789012'
START='2026-09-28 12:00:00'


def fixture():
    source=dict(item=I,sku=S,state='异常',marketing_id='999999999',current_price='1000',min_final='799.9')
    a=dict(item=I,sku=S,custom=False,activity_price='1000',target='800',big_target='700')
    d=dict(item=I,sku=S,custom=False,daily='1000',deduct='100.10',target='800',final='799.90')
    lists={(I,m):dict(complete=True,offers=[]) for m in ('商品级','SKU级')}
    return [source],[a],[d],[],lists,{},[],dict(start=START,end=c.WINDOW['end'])


def page(item=I,mode='商品级'):
    return dict(item=item,mode=mode,platform_write=False,observed=dict(
        filters=[dict(placeholder='商品ID：逗号或空格隔开',value=item),dict(placeholder='活动ID',value=''),dict(placeholder='商品名称',value='')],
        selects=[],radios=[dict(text=mode,checked=True)],
        pagers=[dict(classes='qn-pagination-style',controls=[dict(classes='next-current',label='第1页，共1页'),dict(classes='next-next',disabled=True),dict(classes='next-prev',disabled=True)])],
        headers=['商品信息优惠级别活动优惠1件预估价（公域）活动信息活动状态活动时间操作'],
        rows=[dict(cells=['没有数据'],text='没有数据',key=None)],empty=['没有数据']))


def offer():
    return dict(offer_id='123456789',mode='SKU级',name='test',start=c.WINDOW['start'],end=c.WINDOW['end'],status='进行中',deduction_summary='已设置')


def test_valid_row_is_ready_but_never_new_signup():
    r=c.partition(*fixture())
    assert len(r['discount_rows'])==1 and not r['signup_rows']


@pytest.mark.parametrize('mode',['商品级','SKU级'])
def test_each_missing_list_blocks_only_its_item(mode):
    args=fixture();args[4].pop((I,mode))
    r=c.partition(*args)
    assert not r['discount_rows'] and 'both_complete_offer_lists_missing' in r['rows'][0]['issues']


def test_foreign_sku_error_does_not_discard_good_peer():
    args=list(fixture());bad=dict(args[0][0],sku='23456789013')
    args[0].append(bad);args[3].append(dict(item=I,sku=bad['sku'],error='mapping_conflict'))
    r=c.partition(*args)
    assert len(r['rows'])==2 and len(r['discount_rows'])==1


@pytest.mark.parametrize('status',['success','unknown'])
def test_registered_receipt_not_cleared_by_current_empty_list(status):
    args=fixture();args[5][I]=status
    r=c.partition(*args)
    assert not r['discount_rows'] and 'registered_success_unknown_or_inflight_do_not_replay' in r['rows'][0]['issues']


def test_overlapping_offer_never_stacked_even_without_amount():
    args=fixture();args[4][I,'SKU级']['offers']=[offer()]
    r=c.partition(*args)
    assert not r['discount_rows'] and r['rows'][0]['overlapping_offers'][0]['offer_id']=='123456789'


def test_disjoint_offer_is_not_a_discount_block():
    args=fixture();args[4][I,'SKU级']['offers']=[dict(offer(),end='2026-09-28 11:59:59')]
    assert len(c.partition(*args)['discount_rows'])==1


def test_boundary_touch_is_overlap():
    args=fixture();args[4][I,'SKU级']['offers']=[dict(offer(),end=START)]
    assert not c.partition(*args)['discount_rows']


def test_price_conflict_in_place_action_not_reenrollment():
    args=fixture();args[0][0]['current_price']='990'
    r=c.partition(*args)
    assert not r['discount_rows'] and not r['signup_rows']
    assert r['price_changes'][0]['new_price']=='1000' and not r['price_changes'][0]['import_supported']


def test_missing_cap_keeps_independent_price_action():
    args=fixture();args[0][0]['current_price']='990';args[3].append(dict(item=I,sku=S,error='current_platform_cap_missing'))
    r=c.partition(*args)
    assert len(r['price_changes'])==1 and not r['discount_rows']


@pytest.mark.parametrize('state',['活动中','已发布设定','未知','草稿'])
def test_success_unknown_and_changed_scope_never_price_modified(state):
    args=fixture();args[0][0].update(current_price='900',state=state)
    r=c.partition(*args)
    assert not r['discount_rows'] and not r['price_changes']


@pytest.mark.parametrize('cap,original,floor,expected', [('660','1000','200','733.33'),('990','1500','300','1100.00')])
def test_two_custom_corrections_keep_fixed_floor(cap,original,floor,expected):
    r=c.custom_price(dict(current_price='2000',min_final=cap),dict(original=original,floor=floor,source='receipt'))
    assert r['activity_price']==expected


@pytest.mark.parametrize('basis',[None,dict(original='1000',floor='20',source='receipt'),dict(original='1000',floor='200',uncertain=True,source='receipt')])
def test_missing_or_rebased_custom_basis_refused(basis):
    with pytest.raises(ValueError):c.custom_price(dict(current_price='2000',min_final='90'),basis)


def test_custom_floor_crossing_requires_rotation():
    with pytest.raises(ValueError,match='requires_rotation'):
        c.custom_price(dict(current_price='2000',min_final='90'),dict(original='1000',floor='200',source='receipt'))


def test_unchanged_custom_has_no_discount_and_no_forced_daily_reset():
    args=list(fixture());args[1][0].update(custom=True,activity_price='1700');args[2]=[]
    args[0][0].update(current_price='500',min_final='500')
    r=c.partition(*args)
    assert not r['discount_rows'] and not r['price_changes'] and r['rows'][0]['disposition']=='定制不加单品立减'


@pytest.mark.parametrize('start',['2026-09-28 00:00:00','2026-09-27 23:59:59','2026-10-07 20:00:00','2026-10-07 19:59:59','2026-09-28 01:00:00'])
def test_no_backdate_or_window_expansion(start):
    with pytest.raises(ValueError):c.future_window(start,datetime(2026,9,28,1,tzinfo=c.TZ))


def test_future_time_accepted():
    assert c.future_window(START,datetime(2026,9,28,1,tzinfo=c.TZ))['end']==c.TARGET_WINDOW['end']


@pytest.mark.parametrize('start',['2026-10-01 00:00:00','2026-10-07 19:59:58'])
def test_future_activation_may_use_authorized_extended_period(start):
    assert c.future_window(start,datetime(2026,9,28,1,tzinfo=c.TZ))==dict(start=start,end='2026-10-07 19:59:59')


@pytest.mark.parametrize('start,end,classification',[
    ('2026-09-28 00:00:00','2026-10-07 19:59:59','original_overlap'),
    ('2026-10-01 00:00:00','2026-10-02 23:59:59','new_overlap'),
    ('2026-10-07 19:59:59','2026-10-08 00:00:00','new_overlap'),
    ('2026-10-07 20:00:00','2026-10-11 23:59:59','disjoint'),
    ('2026-09-27 00:00:00','2026-09-27 23:59:59','disjoint'),
])
def test_full_target_dates_not_original_overlap_flag(start,end,classification):
    lists=fixture()[4]
    lists[I,'SKU级']['offers']=[dict(offer(),start=start,end=end,overlaps_requested_window=False)]
    saved=copy.deepcopy(lists)
    analysis=c.target_intersections(lists,[I])
    assert analysis['counts']=={classification:1} and lists==saved
    assert analysis['extended_period_amount_read'] is False
    args=list(fixture());args[4]=lists;args[-1]=c.TARGET_WINDOW
    assert bool(c.partition(*args)['discount_rows'])==(classification=='disjoint')


def test_new_overlap_missing_amount_blocks_only_its_item():
    args=list(fixture());peer='12345678902'
    for idx in (0,1,2):args[idx].append(dict(args[idx][0],item=peer))
    args[4].update({(peer,m):dict(complete=True,offers=[]) for m in ('商品级','SKU级')})
    args[4][I,'SKU级']['offers']=[dict(offer(),start='2026-10-01 00:00:00',end=c.TARGET_WINDOW['end'])]
    args[-1]=c.TARGET_WINDOW
    result=c.partition(*args)
    assert [r['item'] for r in result['discount_rows']]==[peer]
    assert len(c.target_intersections(args[4],[I,peer])['offers'])==1


def test_full_target_analysis_lists_missing_modes_without_inventing_empty():
    lists=fixture()[4];lists.pop((I,'SKU级'))
    result=c.target_intersections(lists,[I])
    assert result['missing_lists']==[dict(item=I,mode='SKU级',error='not_reached')]


def test_original_request_window_and_hash_are_not_extended(monkeypatch):
    args=terminal_fixture(monkeypatch)
    payload_before=copy.deepcopy(args[-1]);hash_before=c.REQUEST_SHA
    c.target_intersections(fixture()[4],[I])
    assert c.validate_terminal(*args)['price_window']==payload_before['price_window']==c.WINDOW
    assert c.WINDOW['end']=='2026-09-30 23:59:59' and c.REQUEST_SHA==hash_before
    args[-1]['price_window']=c.TARGET_WINDOW
    with pytest.raises(ValueError):c.validate_terminal(*args)


def test_original_period_overlap_before_future_start_is_not_erased():
    args=list(fixture());args[-1]=c.TARGET_WINDOW
    args[4][I,'SKU级']['offers']=[dict(offer(),end='2026-09-28 01:00:00')]
    assert not c.partition(*args)['discount_rows']


def test_full_pages_not_cached_flags_determine_coverage():
    payload=dict(items=[I],price_window=c.WINDOW)
    data=dict(rows=[dict(item=I,mode='商品级',pages=[page()],list_coverage_verified=False)])
    r=c.coverage(data,payload,c.load_checker())
    assert r[I,'商品级']['complete']
    data['rows'][0]['pages']=[];data['rows'][0]['list_coverage_verified']=True
    assert not c.coverage(data,payload,c.load_checker())[I,'商品级']['complete']


def test_one_bad_page_does_not_erase_good_list():
    p=page(mode='SKU级');p['observed']['filters'][0]['value']='other'
    data=dict(rows=[dict(item=I,mode='商品级',pages=[page()]),dict(item=I,mode='SKU级',pages=[p])])
    r=c.coverage(data,dict(items=[I],price_window=c.WINDOW),c.load_checker())
    assert r[I,'商品级']['complete'] and not r[I,'SKU级']['complete']


def test_duplicate_list_is_rejected():
    r=dict(item=I,mode='商品级',pages=[page()])
    with pytest.raises(ValueError):c.coverage(dict(rows=[r,r]),dict(items=[I],price_window=c.WINDOW),c.load_checker())


def terminal_fixture(monkeypatch):
    payload=dict(items=[I],sku_scope={I:[S]},price_window=c.WINDOW,read_request_id='a'*64)
    request=dict(action_id=c.JOB,payload=payload,request_sha=c.fingerprint(payload))
    reqtext=json.dumps(request)
    reqhash=c.fingerprint(dict(operation='discount_item_discovery',request=request))
    monkeypatch.setattr(c,'REQUEST_SHA',reqhash)
    original_raw=b'{"platform_write":false}'
    monkeypatch.setattr(c,'SOURCE_SHA',c.sha(original_raw))
    meta=dict(job_id=c.JOB,source_sha256=c.SOURCE_SHA,original_request_sha=reqhash,price_window=c.WINDOW)
    data=dict(schema='discount-batch-read-v2',state='partial_readback',platform_write=False,source_meta=meta,**payload,rows=[])
    raw=json.dumps(data).encode()
    row=dict(id=c.JOB,state='finished',operation='discount_item_discovery',request_sha=reqhash,request=reqtext,result=json.dumps(data))
    audit=dict(source_sha256=c.SOURCE_SHA,previous_request=reqtext,previous_result=original_raw.decode(),plan=json.dumps(meta))
    return row,audit,raw,original_raw,payload


def test_partial_terminal_is_accepted_only_when_durably_bound(monkeypatch):
    args=terminal_fixture(monkeypatch)
    assert c.validate_terminal(*args)['state']=='partial_readback'


@pytest.mark.parametrize('change',['running','scope','result','audit','original','meta','request_hash'])
def test_terminal_tampering_and_running_are_rejected(monkeypatch,change):
    args=list(terminal_fixture(monkeypatch))
    if change=='running':args[0]['state']='running'
    if change=='scope':args[4]=dict(args[4],items=['999999999'])
    if change=='result':args[0]['result']='{}'
    if change=='audit':args[1]['source_sha256']='0'*64
    if change=='original':args[3]+=b' '
    if change=='meta':args[1]['plan']='{}'
    if change=='request_hash':args[0]['request_sha']='0'*64
    with pytest.raises(ValueError):c.validate_terminal(*args)


def test_readonly_sqlite_cannot_create_table(tmp_path):
    path=tmp_path/'db.sqlite';sqlite3.connect(path).close()
    db=c.readonly(path)
    try:
        with pytest.raises(sqlite3.OperationalError):db.execute('CREATE TABLE forbidden(id)')
    finally:db.close()


def test_source_hash_pinned(tmp_path):
    f=tmp_path/'input';f.write_bytes(b'old')
    assert c.pinned(f,c.sha(b'old'))==b'old'
    with pytest.raises(ValueError):c.pinned(f,c.sha(b'new'))


def test_no_overwrite_before_any_business_read(tmp_path):
    with pytest.raises(ValueError,match='output_exists'):
        c.prepare(START,tmp_path)


def test_running_job_rejected_before_touching_any_result_file(tmp_path):
    db=sqlite3.connect(tmp_path/'jobs.sqlite')
    db.execute('CREATE TABLE campaign_transfer_jobs(id TEXT,state TEXT)')
    db.execute('INSERT INTO campaign_transfer_jobs VALUES(?,?)',(c.JOB,'running'))
    db.commit();db.close()
    with pytest.raises(ValueError,match='not_terminal'):
        c.terminal(tmp_path,{})


def inactive_fixture():
    old=dict(offer(),platform_offer_id='145761399121',items=[dict(item=I,status='unknown')])
    proof=dict(offer_ids=sorted(c.REMOVED_IDS))
    return old,proof


def test_same_batch_double_evidence_only_removes_availability_not_history():
    old,proof=inactive_fixture();saved=copy.deepcopy(old)
    remaining,resolved=c.filter_batch_inactive([old],fixture()[4],proof)
    assert not remaining and len(resolved)==1 and old==saved
    assert resolved[0]['historical_status']=='unknown' and resolved[0]['claim_unchanged']


@pytest.mark.parametrize('case',['missing_proof','unknown_id','actual_active','wrong_old_window','missing_list','id_reappeared','unrelated_item'])
def test_same_batch_absence_cannot_clear_other_unknowns_or_missing_lists(case):
    old,proof=inactive_fixture();lists=fixture()[4]
    if case=='unknown_id':old['platform_offer_id']='bundle:unknown'
    if case=='actual_active':old['platform_offer_id']='146901969223'
    if case=='missing_proof':proof['offer_ids']=[]
    if case=='wrong_old_window':old['end']='2026-10-07 19:59:59'
    if case=='missing_list':lists.pop((I,'SKU级'))
    if case=='id_reappeared':lists[I,'SKU级']['offers']=[dict(offer(),offer_id='145761399121')]
    if case=='unrelated_item':old['items'][0]['item']='999999999'
    remaining,resolved=c.filter_batch_inactive([old],lists,proof)
    assert remaining==[old] and not resolved


def test_prepare_full_403_partial_terminal_writes_only_independent_rows(tmp_path,monkeypatch):
    """Synthetic business data, real fixed writer. No production terminal read."""
    from campaign_price_snapshot import digest
    from campaign_official_template import read_rows
    from io import BytesIO
    from zipfile import ZipFile
    erp,identities,scope=[],[],{}
    for n in range(43):
        item=str(10000000000+n);scope[item]=[]
        for j in range(10 if n<16 else 9):
            sku=str(20000000000+n*100+j);scope[item].append(sku)
            erp.append(dict(item=item,sku=sku,alt=[],code=f'TEST-{n}-{j}',daily='1000.00',medium_target='800.00',big_target='700.00',custom=False))
            identities.append(dict(item=item,sku=sku,marketing_id=str(30000000000+n),state='异常',current_price='1000.00',min_final='799.90'))
    assert len(identities)==403
    evidence=tmp_path/'official.json';evidence.write_bytes(b'{}')
    payload=dict(items=list(scope),sku_scope=scope,price_window=c.WINDOW,read_request_id='a'*64)
    snapshot=dict(all_erp_rows=erp,resolved_price_version_sha256=digest(erp))
    inv=dict(rows=identities,source=dict(path=str(evidence),sha256=c.sha(b'{}')))
    docs=dict(snapshot=snapshot,inventory=inv,calculation=dict(scope=list(scope),sources=[]),request=dict(payload=payload))
    inputs={}
    for name,body in docs.items():
        raw=json.dumps(body).encode();path=tmp_path/(name+'.json');path.write_bytes(raw)
        inputs[name]=(str(path),c.sha(raw))
    master=(Path(__file__).resolve().parents[2]/'backend/app/assets/taobao_templates/single_item_discount.xlsx').read_bytes()
    path=tmp_path/'master.xlsx';path.write_bytes(master)
    inputs.update({k:(str(path),c.sha(master)) for k in ('discount_master','activity_master')})
    monkeypatch.setattr(c,'INPUTS',inputs)
    pages=[dict(item=i,mode=m,pages=[page(i,m)]) for i in scope for m in ('商品级','SKU级')]
    pages[-1]['pages']=[]
    data=dict(rows=pages,state='partial_readback')
    monkeypatch.setattr(c,'terminal',lambda *_:(data,dict(sha256='f'*64)))
    monkeypatch.setattr(c,'current_batch_absence',lambda *_:dict(offer_ids=[]))
    monkeypatch.setattr(c,'current_rejection',lambda:dict(rows=[],required_evidence='synthetic'))
    monkeypatch.setattr(c,'authority_state',lambda *_,**kw:({}, {}, [], []))
    class Clock:
        @staticmethod
        def now(tz):return datetime(2026,9,28,1,tzinfo=tz)
        strptime=datetime.strptime
    monkeypatch.setattr(c,'datetime',Clock)
    out=tmp_path/'prepared'
    result=c.prepare(START,out)
    assert len(result['rows'])==403 and len(result['discount_rows'])==394
    assert result['verified_lists']==85 and result['partial_terminal'] is True
    assert result['target_window']==c.TARGET_WINDOW and result['original_read_window']==c.WINDOW
    assert result['effective_window']['end']=='2026-10-07 19:59:59'
    assert result['pricing']==dict(rate='0.10',target='medium')
    assert result['prior_short_window_file_status']=='SUPERSEDED_BY_OFFICIAL_REJECTION_ATTEMPT_CONTEXT_UNKNOWN'
    assert result['past_period_coverage_claimed'] is False
    assert not result['signup_rows'] and len(result['files'])==1
    generated=(out/result['files'][0]['name']).read_bytes()
    rows=read_rows(generated,'Sheet1')
    assert len(rows)==395 and all(r['C']=='100.10' for n,r in rows.items() if n>1)
    with ZipFile(BytesIO(master)) as a,ZipFile(BytesIO(generated)) as b:
        assert a.namelist()==b.namelist()
        for name in a.namelist():
            if name!='xl/worksheets/sheet1.xml':assert a.read(name)==b.read(name)
    assert (tmp_path/'master.xlsx').read_bytes()==master
    assert not result['business_acceptance'] and not result['platform_write'] and not result['database_write']


def rejection_xml(rows):
    """Synthetic vendor package with wrong A1 dimension, no spreadsheet engine."""
    from io import BytesIO
    from zipfile import ZipFile
    from xml.sax.saxutils import escape
    stream=BytesIO()
    with ZipFile(stream,'w') as z:
        z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="sheet1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        cells=''.join('<row r="'+str(n)+'">'+''.join('<c r="'+col+str(n)+'" t="inlineStr"><is><t>'+escape(str(v))+'</t></is></c>' for col,v in row.items())+'</row>' for n,row in rows.items())
        z.writestr('xl/worksheets/sheet1.xml','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><dimension ref="A1"/><sheetData>'+cells+'</sheetData></worksheet>')
    return stream.getvalue()


def rejection_rows():
    return {1:dict(A='商品Id',B='skuId',C='优惠类型',D='优惠值',E='优惠值取值方式',F='失败原因'),
            2:dict(A=I,B=S,C='减钱',D='100.1',E='默认',F='已经参加了单品立减活动，id：147487959755')}


def test_rejection_bad_dimension_reads_physical_rows_and_decimal_amounts():
    result=c.parse_rejection(rejection_xml(rejection_rows()),{(I,S):'100.10'})
    assert len(result)==1 and result[0]['offer_id']=='147487959755'


@pytest.mark.parametrize('fault',['header','amount','foreign','duplicate','missing','reason','type'])
def test_official_rejection_tamper_never_silently_passes(fault):
    rows=rejection_rows()
    if fault=='header':rows[1]['F']='未知'
    if fault=='amount':rows[2]['D']='99.99'
    if fault=='foreign':rows[2]['A']='99999999999'
    if fault=='duplicate':rows[3]=dict(rows[2])
    if fault=='missing':rows.pop(2)
    if fault=='reason':rows[2]['F']='成功'
    if fault=='type':rows[2]['C']='打折'
    with pytest.raises(ValueError):c.parse_rejection(rejection_xml(rows),{(I,S):'100.10'})


def test_official_rejection_overrides_disjoint_date_analysis_and_unknown_context():
    args=list(fixture());peer='12345678902'
    for idx in (0,1,2):args[idx].append(dict(args[idx][0],item=peer))
    args[4].update({(peer,m):dict(complete=True,offers=[]) for m in ('商品级','SKU级')})
    args[4][I,'SKU级']['offers']=[dict(offer(),offer_id='147487959755',start='2026-10-07 20:00:00',end='2026-10-11 23:59:59')]
    r=c.partition(*args)
    evidence=dict(rows=c.parse_rejection(rejection_xml(rejection_rows()),{(I,S):'100.10'}),submission_saved_window=None)
    c.apply_rejection(r,evidence,c.target_intersections(args[4],[I,peer]))
    assert [x['item'] for x in r['discount_rows']]==[peer]
    assert r['rows'][0]['disposition']=='待处理' and r['official_rejection']['submission_saved_window'] is None
    assert r['rows'][0]['official_rejection']['saved_offer_windows'][0]['classification']=='disjoint'


def test_rejected_all_rows_produces_no_upload_scope_and_does_not_clear_other_issues():
    args=fixture();args[5][I]='unknown'
    r=c.partition(*args)
    evidence=dict(rows=c.parse_rejection(rejection_xml(rejection_rows()),{(I,S):'100.10'}))
    c.apply_rejection(r,evidence,dict(offers=[]))
    assert not r['discount_rows'] and not r['upload_ready']
    assert 'registered_success_unknown_or_inflight_do_not_replay' in r['rows'][0]['issues']


def test_rejection_is_mandatory_before_business_inputs(tmp_path,monkeypatch):
    monkeypatch.setattr(c,'future_window',lambda *_:dict(c.TARGET_WINDOW))
    def missing():raise ValueError('source_changed:official_rejection')
    monkeypatch.setattr(c,'current_rejection',missing)
    with pytest.raises(ValueError,match='official_rejection'):c.prepare(START,tmp_path/'new')
    assert not (tmp_path/'new').exists()


def actual_context():
    return dict(activity_id='147682854338',status='全部导入失败',
                window=dict(start='2026-09-28 01:45:00',end='2026-10-07 23:59:59'),
                conflicting_offers=[dict(offer_id=oid,start='2026-10-07 20:00:00',end='2026-10-11 23:59:59')
                                   for oid in ('147487959755','147633129042')])


def test_actual_saved_window_not_workbook_dates_explains_failure():
    context=actual_context();before=copy.deepcopy(context)
    check=c.saved_window_check(context,dict(start='2026-09-28 01:45:00',end=c.TARGET_WINDOW['end']))
    assert check['differences']==['end'] and not check['matches']
    assert len(check['actual_overlaps'])==2
    for o in check['actual_overlaps']:
        assert o['start']=='2026-10-07 20:00:00' and o['end']=='2026-10-07 23:59:59'
    assert context==before and not check['automatic_retry']


def test_corrected_end_has_no_overlap_but_does_not_invent_save_proof():
    context=actual_context();context['window']['end']=c.TARGET_WINDOW['end']
    check=c.saved_window_check(context,context['window'])
    assert check['matches'] and not check['actual_overlaps']
    assert check['corrected_save_verified'] is False and check['automatic_retry'] is False


@pytest.mark.parametrize('fault',['wrong_activity','success','partial','unknown','end_second','start_mismatch','missing_start','malformed_date','outside_target'])
def test_actual_saved_settings_gate_rejects_wrong_identity_or_window(fault):
    context=actual_context();context['window']=dict(start=START,end=c.TARGET_WINDOW['end'])
    intended=dict(context['window'])
    if fault=='wrong_activity':context['activity_id']='147487959755'
    if fault=='success':context['status']='导入成功'
    if fault=='partial':context['status']='部分导入成功'
    if fault=='unknown':context['status']='未知'
    if fault=='end_second':context['window']['end']='2026-10-07 20:00:00'
    if fault=='start_mismatch':context['window']['start']='2026-09-28 11:59:59'
    if fault=='missing_start':context['window'].pop('start')
    if fault=='malformed_date':context['window']['start']='2026-9-28 12:00:00'
    if fault=='outside_target':context['window']['end']=intended['end']='2026-10-11 23:59:59'
    assert not c.saved_window_check(context,intended)['matches']


def test_confirmed_cause_replaces_unknown_but_keeps_hold_until_correct_save():
    result=c.partition(*fixture());result['effective_window']=dict(start=START,end=c.TARGET_WINDOW['end'])
    evidence=dict(rows=c.parse_rejection(rejection_xml(rejection_rows()),{(I,S):'100.10'}),
                  submission_context=actual_context(),cause='actual_saved_end_overlaps_october88')
    c.apply_rejection(result,evidence,dict(offers=[]))
    assert not result['discount_rows'] and result['upload_ready'] is False
    assert result['prior_short_window_file_status']=='HOLD_UNTIL_EXACT_ACTIVITY_WINDOW_CORRECTED'
    assert result['rows'][0]['issues']==['actual_saved_activity_window_mismatch_requires_corrected_save']
    assert result['saved_window_check']['actual_overlaps']
