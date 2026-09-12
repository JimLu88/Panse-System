"""Concrete adapter tests: isolated SQLite/files and bounded Edge jobs only."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock,patch
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from campaign_continuous_transport import CampaignTransport,persist
from campaign_continuous_policy import RULE_SHA
from campaign_continuous_repairs import table,verify_claim,apply_verified_amendments
from campaign_entry_authority import Authority,file_sha
from campaign_continuous_execute import validate_request
from test_campaign_segmented_time import request as time_request


def test_rate_ten_and_fifteen_not_trimmed_to_one(tmp_path):
    transport=CampaignTransport(None,None,root=tmp_path,request={},artifact_roots=[tmp_path])
    for rate,label in [('0.1','10%'),('0.12','12%'),('0.15','15%')]:
        p={k:'value' for k in ('campaign_id','phase_id','sign_record_id','title','phase_title','start','end')}
        p.update(shop_id='test',official_rate=rate)
        assert transport.identity({'identity':p})['rate_label']==label


def test_job_ack_is_not_terminal_and_saved_job_never_submitted_again(tmp_path):
    edge=Mock();edge.submit.return_value={'job_id':'a'*64,'state':'running'}
    edge.wait.return_value={'job_id':'a'*64,'state':'unknown'}
    t=CampaignTransport(edge,None,root=tmp_path,request={},artifact_roots=[tmp_path])
    with pytest.raises(ValueError,match='not_terminal'):t.job('signup',{},tmp_path)
    edge.status.return_value=edge.wait.return_value
    with pytest.raises(ValueError,match='not_terminal'):t.job('signup',{},tmp_path)
    assert edge.submit.call_count==1


def test_immutable_evidence_and_wrong_rule(tmp_path):
    p=tmp_path/'one.json';persist(p,{'one':1})
    with pytest.raises(ValueError,match='immutable'):persist(p,{'one':2})
    t=CampaignTransport(None,None,root=tmp_path,request={},artifact_roots=[tmp_path])
    with pytest.raises(ValueError,match='not_approved'):t.execute('scope','a',{'rule_sha':'bad'})


def test_shared_product_export_reuses_file_but_checks_each_campaign_history(tmp_path):
    source=tmp_path/'shared';persist(source/'resolved-snapshot.json',{'price':'same'})
    a=Mock();a.blocked.return_value={'22':'success'};a.db.execute.return_value=[]
    t=CampaignTransport(Mock(),a,root=tmp_path/'segment',request={},artifact_roots=[tmp_path])
    t.shared_scope=({'price_version':'same','prior_outcomes':{'old':'success'}},source)
    p={'identity':{'campaign_id':'1','phase_id':'2','sign_record_id':'3','start':'start','end':'end'}}
    r=t.step_scope('a',p,tmp_path/'stage')
    assert r['prior_outcomes']=={'22':'success'}
    a.blocked.assert_called_once_with('1/2/3','signup','start','end')
    t.edge.submit.assert_not_called()


def test_final_test_reuses_exact_verified_prefetch_without_second_download(tmp_path):
    existing={'job_id':'a'*64,'snapshot_request_id':'b'*64}
    edge=Mock();edge.status.return_value={'job_id':existing['job_id'],'state':'finished'}
    a=Mock();a.resolve_snapshot.return_value={'all_erp_rows':[],
        'current_sellable_item_ids':['1'],'resolved_price_version_sha256':'same'}
    t=CampaignTransport(edge,a,root=tmp_path,request={'existing_product_export':existing},artifact_roots=[tmp_path])
    t.with_prior=lambda value,*args:value
    p={'identity':{'shop_id':'test-shop'}}
    with patch('campaign_price_snapshot.load_rows',return_value=[]),patch(
            'campaign_price_snapshot.build_snapshot',return_value={}),patch(
            'campaign_product_scope.from_edge_job',return_value={'complete':True,'page_evidence':'proof'}) as verify,patch(
            'campaign_product_scope.unique_mappings',return_value={'matches':[],'unknown':[]}):
        result=t.step_scope('c'*64,p,tmp_path/'action')
    assert result['complete']
    assert verify.call_args.kwargs['expected_request_id']=='b'*64
    edge.status.assert_called_once_with('a'*64)
    edge.submit.assert_not_called();edge._action.assert_not_called()


def test_request_binds_discovery_to_calendar_not_arbitrary_window(tmp_path):
    cal=time_request();pages={}
    for c in [cal['daily_activity'],*cal['campaigns']]:
        i,j,k=c['campaign'].split('/')
        pages[c['campaign']]=dict(c,campaign_id=i,phase_id=j,sign_record_id=k,title='test',phase_title='phase',
            entry='https://myseller.taobao.com/home.htm/starb/tmc-next/sale/seller/homepage.htm',
            url='https://myseller.taobao.com/activity?id='+i)
    req={'schema':'continuous_campaign_request_v1','rule_sha':RULE_SHA,'calendar':cal,'pages':pages,
         'observed_links':[p['url'] for p in pages.values()]}
    assert validate_request(req)=='test-shop'
    from campaign_continuous_execute import register_request
    first=register_request(req,tmp_path)
    assert first==register_request(req,tmp_path) and first['platform_write'] is False
    assert len(list((tmp_path/'requests').glob('*.json')))==1
    cached=deepcopy(req);cached['existing_product_export']={'job_id':'a'*64,'snapshot_request_id':'b'*64}
    assert validate_request(cached)=='test-shop'
    cached['existing_product_export']['path']='arbitrary.xlsx'
    with pytest.raises(ValueError,match='exact_existing'):validate_request(cached)
    bad=deepcopy(req);bad['calendar']['campaigns'][0]['end']='2026-09-28 23:59:59'
    with pytest.raises(ValueError,match='not_bound'):validate_request(bad)


@pytest.fixture
def repair(tmp_path):
    manifest=tmp_path/'sources.json';persist(manifest,{'sources':[]})
    a=Authority(tmp_path/'a.sqlite3',manifest);table(a)
    error={'item':'12','sku':'34','terminal':'failed','batch':'55','official_evidence':'official',
           'message':'price','kind':'coupon_price','custom':False,'erp_daily':'100','submitted_price':'100',
           'erp_final_target':'70','feasible_final_price':'69','current_deduct':'18','proposed_deduct':'19'}
    rp=tmp_path/'report.json';persist(rp,{'errors':[error]})
    body={'rule_sha':RULE_SHA,'campaign':'1/2/3','start':'2026-10-01 00:00:00','end':'2026-10-07 23:59:59',
          'report_path':str(rp),'sources':[{'path':str(rp),'sha256':file_sha(rp)}],
          'rows':[{'item':'12','sku':'34','offer_id':'56','old_deduct':'18','new_deduct':'19'}]}
    a.db.execute('INSERT INTO continuous_discount_repairs VALUES(?,?,?,NULL,NULL)',('a'*32,json.dumps(body),'claimed_not_dispatched'))
    offers=[{'offer_id':'56','start':body['start'],'end':body['end'],'items':[{'item':'12','status':'success'}],
             'rows':[{'item':'12','sku':'34','deduct':'18'}]}]
    with patch.object(a,'blocked',return_value={}),patch.object(a,'discount_offers',return_value=offers):
        yield a,body,offers
    a.close()


def test_repair_reclassifies_then_consumes_once(repair):
    a,body,offers=repair
    assert verify_claim(a,'a'*32)['verified_claim']
    result=verify_claim(a,'a'*32,consume_job='b'*64)
    assert result['dispatch_consumed']
    with pytest.raises(ValueError,match='do_not_replay'):verify_claim(a,'a'*32,consume_job='b'*64)


@pytest.mark.parametrize('bad',[None,'amount','window','job','duplicate','source'])
def test_amend_readback_reconciliation_never_replays(repair,tmp_path,bad):
    from campaign_continuous_repairs import reconcile_finished_amend
    a,body,offers=repair;cid='a'*32;jid='b'*64
    verify_claim(a,cid,consume_job=jid)
    row={'offer_id':'56','item':'12','values':{'34':'19'},'window':{'start':body['start'],'end':body['end']}}
    proof={'state':'verified_saved','claim_id':cid,'job_id':jid,'rows':[row]}
    if bad=='amount':row['values']['34']='20'
    if bad=='window':row['window']['end']='2026-10-08 23:59:59'
    if bad=='job':proof['job_id']='c'*64
    if bad=='duplicate':proof['rows'].append(deepcopy(row))
    if bad=='source':Path(body['report_path']).write_text('{}')
    path=tmp_path/'amend-proof.json';persist(path,proof)
    job={'job_id':jid,'operation':'discount_amend','state':'finished','result':dict(proof,evidence_path=str(path))}
    if bad:
        with pytest.raises(ValueError):reconcile_finished_amend(a,cid,job)
        assert a.db.execute('SELECT state FROM continuous_discount_repairs').fetchone()[0]=='dispatched_unknown'
    else:
        assert reconcile_finished_amend(a,cid,job)==['12']
        assert reconcile_finished_amend(a,cid,job)==['12']
        assert apply_verified_amendments(a,offers)[0]['rows'][0]['deduct']=='19'


def test_repair_rejects_old_amount_drift_and_two_yuan_excess(repair):
    a,body,offers=repair
    offers[0]['rows'][0]['deduct']='17'
    with pytest.raises(ValueError,match='old_amount_changed'):verify_claim(a,'a'*32)
    offers[0]['rows'][0]['deduct']='18';body['rows'][0]['new_deduct']='30'
    a.db.execute('UPDATE continuous_discount_repairs SET body=?',(json.dumps(body),))
    with pytest.raises(ValueError,match='outside_frozen_rule'):verify_claim(a,'a'*32)


def test_fixed_legacy_identity_is_namespaced_not_fake_platform_number():
    from campaign_entry_authority import exact_campaign
    assert exact_campaign('legacy/itemApply/3172207691')=='legacy/itemApply/3172207691'
    with pytest.raises(ValueError):exact_campaign('legacy/unknown/3172207691')


def test_one_active_sku_does_not_mark_entire_product_registered(tmp_path):
    source=tmp_path/'template.xlsx';source.write_bytes(b'test-only')
    result={'state':'downloaded','source':'official_current_template_download',
            'path':str(source),'sha256':file_sha(source),'items':['1','2']}
    t=CampaignTransport(Mock(),None,root=tmp_path,request={},artifact_roots=[tmp_path])
    t.identity=lambda p:{};t.job=lambda *a:{'result':result}
    rows=[{'item':'1','state':'活动中'},{'item':'1','state':'失败'},{'item':'2','state':'活动中'}]
    with patch('campaign_official_template.template_rows',return_value=rows):
        assert t.step_template('a',{'items':['1','2']},tmp_path)['registered_items']==['2']


def test_fixed_master_does_not_claim_current_enrollment_status(tmp_path):
    source=tmp_path/'template.xlsx';source.write_bytes(b'test-only')
    result={'state':'downloaded','source':'official_current_template_download','fixed_master':True,
            'path':str(source),'sha256':file_sha(source),'items':['1']}
    t=CampaignTransport(None,None,root=tmp_path,request={'fixed_signup_template':'manifest'},artifact_roots=[tmp_path])
    t.identity=lambda p:{}
    with patch('campaign_fixed_signup_template.resolve_master',return_value=result):
        assert t.step_template('a',{'items':['1']},tmp_path)['registered_items']==[]
