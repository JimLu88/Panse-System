import asyncio
import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest

BASE=Path('D:/AI/畔色ERP系统')
sys.path[:0]=[str(Path(__file__).parent),str(BASE/'outputs/campaign-nov88-anomaly-repair-20261117/program'),str(BASE/'Web-Agent程序')]
import repair_resume as m

@pytest.mark.asyncio
async def test_real_disabled_price_dom_is_read_without_enabling_or_filling():
    from playwright.async_api import async_playwright
    shape=m.c.load(BASE/'outputs/campaign-nov88-anomaly-repair-20261117/resume-1/diagnose/shape.json')
    table=next(t for t in shape['tables'] if 'skuList.0.activityPrice' in t)
    async with async_playwright() as p:
        browser=await p.chromium.launch(channel='msedge',headless=True)
        page=await browser.new_page()
        await page.route('**/*',lambda route:route.abort())
        await page.set_content(table)
        before=await page.locator('input[id$=".activityPrice"]').evaluate_all('(es)=>es.map(e=>({value:e.value,disabled:e.disabled,readonly:e.readOnly}))')
        assert len(before)==11 and all(r['disabled'] for r in before)
        assert (await page.evaluate(m.DOUBLE_ROWS))['rows']==[]
        observed=await m.readonly_inventory(page,set(m.confirmed_first()['prices']))
        assert m.prices(observed)==m.confirmed_first()['prices']
        after=await page.locator('input[id$=".activityPrice"]').evaluate_all('(es)=>es.map(e=>({value:e.value,disabled:e.disabled,readonly:e.readOnly}))')
        assert after==before
        await browser.close()

def fixture_confirmation(monkeypatch,tmp_path):
    monkeypatch.setattr(m.c,'ROOT',tmp_path)
    record=dict(item=m.FIRST,marketing_id='111111111',rows=[dict(sku='a',activity_price='1000'),dict(sku='b',activity_price='300')])
    request={'records':[record]}
    plan={'decisions':[dict(item=m.FIRST,sku='a',action='original_activity_price',new_price='500.00',final='440.00')]}
    m.c.save(tmp_path/'plan.json',plan)
    m.c.save(tmp_path/'prepare/terminal.json',{'plan':m.c.reference(tmp_path/'plan.json')})
    m.c.save(tmp_path/'apply/terminal.json',{'failed_items':[dict(item=m.FIRST,reason='original_editor_step_not_ready',state='unknown_no_replay')]})
    folder=tmp_path/'apply'/m.FIRST/'original'
    expected={'a':'500.00','b':'300.00'}
    m.c.save(folder/'write-intent.json',dict(marketing_id=record['marketing_id'],after=expected,authorization=m.c.AUTH))
    for name in ('submission-claimed.json','final-submit-claimed.json'):
        m.c.save(folder/name,dict(expected=expected,authorization=m.c.AUTH))
    m.c.save(folder/'readback/row.json',{'rows':[dict(text=m.FIRST+' '+record['marketing_id'],cells=['活动中','编辑商品'])]})
    m.c.save(tmp_path/'apply/final-inventory/result.json',{'file':{'test':True}})
    rows=[dict(item=m.FIRST,sku=k,marketing_id=record['marketing_id'],activity_price=v,coupon_price='440.00',state='活动中') for k,v in expected.items()]
    monkeypatch.setattr(m.c,'validate_request',lambda *a,**k:request)
    monkeypatch.setattr(m.c,'export_rows',lambda *a:rows)
    monkeypatch.setattr(m.c,'verify_export',lambda *a:None)
    return rows

def test_confirmed_first_uses_full_export_and_original_mid(monkeypatch,tmp_path):
    fixture_confirmation(monkeypatch,tmp_path)
    result=m.confirmed_first()
    assert result['no_resubmission'] and result['sku_count']==2

@pytest.mark.parametrize('mutation',['missing','wrong_mid','wrong_price','unknown_status','wrong_coupon'])
def test_first_unknown_or_changed_evidence_blocks_resume(monkeypatch,tmp_path,mutation):
    rows=fixture_confirmation(monkeypatch,tmp_path)
    if mutation=='missing':rows.pop()
    elif mutation=='wrong_mid':rows[0]['marketing_id']='other'
    elif mutation=='wrong_price':rows[1]['activity_price']='999'
    elif mutation=='unknown_status':rows[0]['state']='异常'
    else:rows[0]['coupon_price']='441'
    with pytest.raises(m.original.SurfaceError):m.confirmed_first()

def plan_fixture():
    decisions=[dict(item=m.FIRST,sku='first',action='original_activity_price')]
    for i,item in enumerate(m.REMAINING):
        decisions += [dict(item=item,sku=str(n),action='original_activity_price') for n in range(7 if i==0 else 2 if i==3 else 1)]
    return dict(decisions=decisions,exceptions=[{'known':'small-table'}])

def test_scope_filters_first_and_rejects_added_or_missing_item():
    plan=plan_fixture()
    groups=m.remaining_groups(plan)
    assert tuple(groups)==m.REMAINING and m.FIRST not in groups and sum(map(len,groups.values()))==13
    for changed in (plan['decisions'][:-1],plan['decisions']+[dict(item='other',sku='new',action='original_activity_price')]):
        with pytest.raises(m.original.SurfaceError):m.remaining_groups(dict(plan,decisions=changed))

def setup_apply(monkeypatch,tmp_path,mode=None):
    monkeypatch.setattr(m,'ROOT',tmp_path)
    plan=plan_fixture(); groups=m.remaining_groups(plan)
    request={'records':[dict(item=item) for item in (*m.REMAINING,m.FIRST)],'inventory':{'initial':True}}
    monkeypatch.setattr(m,'checked',lambda *a,**kw:(request,plan,{},groups))
    monkeypatch.setattr(m,'accepted_inspection',lambda *a:{})
    m.c.save(tmp_path/'binding.json',{})
    m.c.save(tmp_path/'inspect/terminal.json',dict(state='readonly_saved_prices_verified',submitted=False))
    calls=[];exports=[]
    async def amend(session,record,changes,folder,validator):
        assert record['item']!=m.FIRST
        calls.append(record['item']); m.c.save(folder/'write-intent.json',{'unknown':True})
        if mode=='unknown' and len(calls)==1: raise RuntimeError('post_save_unavailable')
        if mode=='rejected' and len(calls)==1: return dict(state='official_rejected',reason='official_rejected',item=record['item'])
        return dict(state='saved_prices_verified')
    async def export(session,request,folder):
        exports.append(str(folder)); return {'new':True},[]
    monkeypatch.setattr(m,'amend_original',amend)
    monkeypatch.setattr(m.original,'final_export',export)
    monkeypatch.setattr(m.c,'export_rows',lambda ref:[])
    monkeypatch.setattr(m.c,'audit',lambda *a:dict(rows=[dict(item=i,verified=True) for i in (*m.REMAINING,m.FIRST)],all_verified=True,all_final_prices_known=False))
    return SimpleNamespace(lock=asyncio.Lock()),calls,exports

@pytest.mark.asyncio
async def test_continuation_runs_only_six_once_and_exports_afterwards(monkeypatch,tmp_path):
    session,calls,exports=setup_apply(monkeypatch,tmp_path)
    result=await m.apply(session,'sha')
    assert calls==list(m.REMAINING) and len(exports)==1
    assert result['inherited_completed_items']==[m.FIRST] and result['completed_items']==list(m.REMAINING)
    assert result['state']=='resumed_scope_verified_with_known_exceptions' and not result['all_final_prices_known']
    assert result['final_export']=={'new':True}
    with pytest.raises(FileExistsError):await m.apply(session,'sha')
    assert calls==list(m.REMAINING) and len(exports)==1

@pytest.mark.asyncio
async def test_unknown_write_stops_without_replaying_or_advancing(monkeypatch,tmp_path):
    session,calls,exports=setup_apply(monkeypatch,tmp_path,'unknown')
    result=await m.apply(session,'sha')
    assert calls==[m.REMAINING[0]] and len(exports)==1
    assert result['state']=='stopped_unknown_no_replay' and result['completed_items']==[]

@pytest.mark.asyncio
async def test_official_rejection_isolated_and_others_continue(monkeypatch,tmp_path):
    session,calls,exports=setup_apply(monkeypatch,tmp_path,'rejected')
    result=await m.apply(session,'sha')
    assert calls==list(m.REMAINING) and len(exports)==1
    assert result['state']=='finished_with_failures' and len(result['completed_items'])==5

@pytest.mark.asyncio
async def test_claim_or_terminal_returns_without_start_or_freshness_recheck(monkeypatch,tmp_path):
    monkeypatch.setattr(m,'ROOT',tmp_path)
    monkeypatch.setattr(m.c,'validate_request',lambda *a,**k:{})
    def forbidden(*a,**k):raise AssertionError('must not restart')
    monkeypatch.setattr(m,'checked',forbidden)
    m.c.save(tmp_path/'apply/intent.json',{'original':True})
    result=await m.handle(None,'program_nov88_anomaly_resume_apply',{'request_sha256':'sha'})
    assert result['state']=='already_started_no_replay'
    m.c.save(tmp_path/'apply/terminal.json',{'state':'stopped_unknown_no_replay'})
    result=await m.handle(None,'program_nov88_anomaly_resume_apply',{'request_sha256':'sha'})
    assert result['already_completed_no_replay']

@pytest.mark.asyncio
async def test_first_item_write_is_rejected_before_opening(monkeypatch,tmp_path):
    with pytest.raises(m.original.SurfaceError,match='not_authorized'):
        await m.amend_original(None,dict(item=m.FIRST),[],tmp_path/'first',lambda:None)
    assert not (tmp_path/'first').exists()

def test_changed_release_source_stops_before_any_browser(monkeypatch,tmp_path):
    monkeypatch.setattr(m,'ROOT',tmp_path)
    source=tmp_path/'source.py';source.write_text('before')
    ref=m.c.reference(source)
    m.c.save(tmp_path/'release.json',dict(request_sha256='sha',files=[ref]))
    source.write_text('changed')
    with pytest.raises(m.original.SurfaceError,match='resume_release_source_changed'):m.checked('sha')

def test_inspection_is_bound_to_same_request_and_release(monkeypatch,tmp_path):
    monkeypatch.setattr(m,'ROOT',tmp_path)
    m.c.save(tmp_path/'release.json',{'version':1})
    m.c.save(tmp_path/'inspect/terminal.json',dict(state='readonly_saved_prices_verified',submitted=False,
        request_sha256='sha',release=m.c.reference(tmp_path/'release.json')))
    assert m.accepted_inspection('sha')['submitted'] is False
    with pytest.raises(m.original.SurfaceError,match='release_changed'):m.accepted_inspection('another')
    (tmp_path/'release.json').write_text('{"version":2}')
    with pytest.raises(m.original.SurfaceError,match='release_changed'):m.accepted_inspection('sha')

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['saved','other_changed','readback_unknown'])
async def test_original_amend_uses_readonly_result_and_preserves_unknown(monkeypatch,tmp_path,mode):
    from app.engine import campaign_completion_submit as submission
    values={'a':'1000.00','b':'300.00'}; calls=[]; checks=[]
    record=dict(item=m.REMAINING[0],marketing_id='222222222',rows=[dict(sku=k,activity_price=v) for k,v in values.items()])
    def observed():return {'rows':[dict(sku_id=k,price=v,other_inputs=[{'value':'30','checked':False,'type':'text'}]) for k,v in values.items()]}
    class Page:
        async def close(self):pass
    class Recorder:
        def __init__(self,*a,**kw):pass
        async def start(self):pass
        async def stop(self):return {'test':True}
    async def pace(*a):pass
    async def before(*a):return Page(),SimpleNamespace(_pace=pace),observed(),'异常'
    async def after(*a):
        checks.append('readonly')
        if mode=='readback_unknown':raise m.original.SurfaceError('readback_unknown')
        data=observed()
        if mode=='other_changed':data['rows'][0]['other_inputs'][0]['value']='90'
        return Page(),None,data,'活动中'
    async def inv(*a):return observed()
    class Field:
        async def fill(self,v,**kw):values['a']=v
        async def press(self,*a):pass
    async def field(*a):return Field()
    async def submit(*a,**kw):calls.append('submit')
    monkeypatch.setattr(m.original,'open_editor',before)
    monkeypatch.setattr(m,'open_readback',after)
    monkeypatch.setattr(m.original,'inventory',inv)
    monkeypatch.setattr(m.original,'exact_field',field)
    monkeypatch.setattr(m.original,'RetainedCampaignRecorder',Recorder)
    monkeypatch.setattr(submission,'submit_once',submit)
    folder=tmp_path/'original';changes=[dict(sku='a',action='original_activity_price',new_price='500.00')]
    if mode=='saved':
        result=await m.amend_original(None,record,changes,folder,lambda:None)
        assert result['state']=='saved_prices_verified' and values['b']=='300.00'
        with pytest.raises(FileExistsError):await m.amend_original(None,record,changes,folder,lambda:None)
    else:
        with pytest.raises(m.original.SurfaceError):await m.amend_original(None,record,changes,folder,lambda:None)
        assert (folder/'write-intent.json').exists() and not (folder/'terminal.json').exists()
    assert calls==['submit'] and checks==['readonly']
