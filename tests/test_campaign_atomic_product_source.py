import hashlib
import json
import pytest
def validate(tool, payload):
    return fingerprint(['atomic',tool,{k:v for k,v in payload.items() if k!='request_id'}])
from campaign_atomic_product_source import from_atomic_export, resolve, validate_reference
from campaign_continuous_policy import fingerprint
SurfaceError=ValueError


def fixture(tmp_path):
    identity=dict(campaign_id='49594',phase_id='49600',sign_record_id='3548822475',
        title='26年淘宝10月超级88',phase_title='超级88现货',shop_name='畔色木作',
        start='2026-10-07 20:00:00',end='2026-10-12 23:59:59',rate_label='12%')
    p={'identity':identity,'request_id':'export-1','snapshot_version':'current-1'}
    job_id=validate('sellable_product_export',p)
    folder=tmp_path/job_id;folder.mkdir()
    file=folder/'page-1.xlsx';file.write_bytes(b'fixture-workbook')
    scope={'on_sale':True,'page':1,'page_count':1,'total':1,'item_ids':['123']}
    entry={'scope':scope,'record':{'id':'555','rowCount':1},'path':str(file),
           'sha256':hashlib.sha256(file.read_bytes()).hexdigest()}
    result={'tool_id':'sellable_product_export','state':'succeeded','identity':identity,
            'result':{'state':'downloaded','page_count':1,'observed_total':1,
                      'observed_item_ids':['123'],'files':[entry]}}
    job={'job_id':job_id,'operation':'campaign_atomic','state':'finished','result':result}
    request={'tool_id':'sellable_product_export','payload':p,'request_sha':fingerprint(p)}
    (folder/'page-1-request.json').write_text(json.dumps({'scope':scope,'before_ids':['444']}),encoding='utf-8')
    (folder/'page-1-downloaded.json').write_text(json.dumps(entry),encoding='utf-8')
    (folder/'atomic-result.json').write_text(json.dumps(result),encoding='utf-8')
    options={'identity':identity,'snapshot':'current-1','root':tmp_path,
             'parse_export':lambda raw:[{'item':'123'}],
             'complete_scope':lambda files,**kwargs:{'complete':True,**kwargs}}
    return job,request,options,folder,entry


def test_native_atomic_source_keeps_real_operation(tmp_path):
    job,request,options,_,_=fixture(tmp_path)
    result=from_atomic_export(job,request,**options)
    assert result['complete'] is True
    assert result['page_evidence']['operation']=='campaign_atomic'
    assert result['page_evidence']['snapshot_version']=='current-1'


@pytest.mark.parametrize('kind',['unfinished','wrong_tool','wrong_identity','wrong_snapshot','request_hash',
    'saved_drift','file_drift','old_record','download_receipt','outside_root','page_count','row_count','wrong_file_items'])
def test_invalid_atomic_sources_are_rejected(tmp_path,kind):
    job,request,options,folder,entry=fixture(tmp_path)
    if kind=='unfinished':job['state']='running'
    if kind=='wrong_tool':request['tool_id']='activity_template'
    if kind=='wrong_identity':options['identity']={}
    if kind=='wrong_snapshot':options['snapshot']='different'
    if kind=='request_hash':request['request_sha']='0'*64
    if kind=='saved_drift':job['result']['extra']='drift'
    if kind=='file_drift':(folder/'page-1.xlsx').write_bytes(b'changed')
    if kind=='old_record':(folder/'page-1-request.json').write_text(json.dumps({'scope':entry['scope'],'before_ids':['555']}))
    if kind=='download_receipt':(folder/'page-1-downloaded.json').write_text('{}')
    if kind=='outside_root':
        outside=tmp_path/'other.xlsx';outside.write_bytes(b'fixture-workbook');entry['path']=str(outside)
    if kind=='page_count':job['result']['result']['page_count']=2
    if kind=='row_count':entry['record']['rowCount']=2
    if kind=='wrong_file_items':options['parse_export']=lambda raw:[{'item':'999'}]
    if kind in ('outside_root','page_count','row_count'):
        (folder/'atomic-result.json').write_text(json.dumps(job['result']))
        (folder/'page-1-downloaded.json').write_text(json.dumps(entry))
    with pytest.raises(SurfaceError):from_atomic_export(job,request,**options)


def test_reference_keeps_legacy_hex_and_explicit_atomic():
    assert validate_reference({'job_id':'a'*64,'snapshot_request_id':'b'*64}) is False
    assert validate_reference({'kind':'atomic','job_id':'a'*64,'snapshot_request_id':'current-1'}) is True
    for bad in ({'job_id':'a'*64,'snapshot_request_id':'current-1'},
                {'kind':'atomic','job_id':'../evil','snapshot_request_id':'current-1'},
                {'kind':'other','job_id':'a'*64,'snapshot_request_id':'b'*64}):
        with pytest.raises(ValueError):validate_reference(bad)


def ledger_fixture(tmp_path,monkeypatch):
    import sqlite3
    import campaign_product_scope
    job,request,options,folder,entry=fixture(tmp_path)
    db=sqlite3.connect(tmp_path/'jobs.sqlite')
    db.execute('CREATE TABLE campaign_transfer_jobs(id TEXT,operation TEXT,state TEXT,request TEXT,result TEXT,request_sha TEXT)')
    db.execute('INSERT INTO campaign_transfer_jobs VALUES(?,?,?,?,?,?)',
        (job['job_id'],job['operation'],job['state'],json.dumps(request),json.dumps(job['result']),
        fingerprint({'operation':'campaign_atomic','request':request})))
    db.commit();db.close()
    monkeypatch.setattr(campaign_product_scope,'parse_export',options['parse_export'])
    monkeypatch.setattr(campaign_product_scope,'complete_scope',options['complete_scope'])
    return job,request,options,{'kind':'atomic','job_id':job['job_id'],'snapshot_request_id':'current-1'}


def test_resolve_native_original_without_browser(tmp_path,monkeypatch):
    job,request,options,ref=ledger_fixture(tmp_path,monkeypatch)
    result=resolve(job,reference=ref,identity=options['identity'],roots=[tmp_path])
    assert result['complete'] is True
    assert result['page_evidence']['job_id']==job['job_id']


@pytest.mark.parametrize('kind',['wrong_ledger_hash','wrong_live_result','wrong_shop','wrong_snapshot','missing_ledger','unknown_campaign'])
def test_resolve_rejects_mixed_sources(tmp_path,monkeypatch,kind):
    import sqlite3
    job,request,options,ref=ledger_fixture(tmp_path,monkeypatch)
    identity=dict(options['identity']);allowed=None
    if kind=='wrong_ledger_hash':
        with sqlite3.connect(tmp_path/'jobs.sqlite') as db:db.execute("UPDATE campaign_transfer_jobs SET request_sha='wrong'")
    if kind=='wrong_live_result':job['result']['extra']='changed'
    if kind=='wrong_shop':identity['shop_name']='other'
    if kind=='wrong_snapshot':ref['snapshot_request_id']='different'
    if kind=='missing_ledger':(tmp_path/'jobs.sqlite').unlink()
    if kind=='unknown_campaign':allowed=[]
    with pytest.raises(ValueError):resolve(job,reference=ref,identity=identity,roots=[tmp_path],allowed_identities=allowed)


def test_same_shop_scope_can_serve_another_explicit_calendar_segment(tmp_path,monkeypatch):
    job,request,options,ref=ledger_fixture(tmp_path,monkeypatch)
    current=dict(options['identity'],campaign_id='other')
    assert resolve(job,reference=ref,identity=current,roots=[tmp_path],allowed_identities=[options['identity'],current])['complete']


def test_controller_consumes_native_scope_without_reexport(tmp_path,monkeypatch):
    import sys
    from types import SimpleNamespace
    import campaign_product_scope
    from campaign_continuous_transport import CampaignTransport
    job,request,options,ref=ledger_fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(campaign_product_scope,'from_edge_job',lambda *a,**k:pytest.fail('legacy adapter called'))
    monkeypatch.setattr(campaign_product_scope,'unique_mappings',lambda *a:{'matches':[],'unknown':[]})
    monkeypatch.setitem(sys.modules,'campaign_catalog_alias_refresh',SimpleNamespace(refresh=lambda authority,snapshot,*a:(snapshot,None)))
    runner=CampaignTransport.__new__(CampaignTransport)
    runner.request={'existing_product_export':ref};runner.roots=[tmp_path];runner.root=tmp_path/'controller'
    runner.authority=object();runner.with_prior=lambda scope,*a:scope
    p=dict(options['identity']);p['shop_id']=p.pop('shop_name');p.pop('rate_label');p['official_rate']='0.12'
    snapshot={'all_erp_rows':[],'current_sellable_item_ids':['123'],'resolved_price_version_sha256':'version'}
    result=runner.finish_exported_scope(snapshot,job,'current-1',{'identity':p},tmp_path/'action')
    assert result['complete'] and result['price_version']=='version'
    assert result['page_evidence']['operation']=='campaign_atomic'
    assert (runner.root/'product-scope.json').is_file()
