import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
B=Path('D:/AI/畔色ERP系统')
sys.path[:0]=[str(Path(__file__).parent),str(B/'outputs/campaign-nov88-anomaly-repair-20261117/program'),str(B/'Web-Agent程序')]
import repair_remaining as m
import remaining_submit as s

def test_only_reviewed_balance_notice_is_closed():
    assert m.reviewed_balance_notice('重要消息 预警通知 万相台无界版账户余额不足800.00元')
    assert not m.reviewed_balance_notice('重要消息 安全验证 预警通知 万相台无界版账户余额不足800.00元')
    assert not m.reviewed_balance_notice('重要消息 处罚通知')

@pytest.mark.asyncio
@pytest.mark.parametrize('pending',[False,True])
async def test_late_warning_not_hidden_by_static_success_text_and_no_submit_replay(monkeypatch,tmp_path,pending):
    calls=[];state={'step':'warning' if pending else 'before','reads':0}
    item=m.LIFT if pending else m.STONE;record=dict(item=item,marketing_id='123456789')
    base=f'商品 ID: {item} 营销 ID: 123456789 已发布设定即为报名成功'
    class Control:
        def __init__(self,label):self.label=label
        async def is_visible(self):return True
        async def inner_text(self):return '保存成功'
        async def click(self,**kw):
            calls.append(self.label)
            state['step']='after' if self.label=='提交' else 'done'
    class Collection:
        def __init__(self,items):self.items=items
        async def all(self):return self.items
    class Body:
        async def inner_text(self,**kw):
            if state['step']=='after':
                state['reads']+=1
                if state['reads']>=2:state['step']='warning'
            return base+(' 商品设价风险提醒 确认无风险，继续提交' if state['step']=='warning' else '')
    class Page:
        def locator(self,selector):return Body() if selector=='body' else Collection([Control('toast')] if state['step']=='done' else [])
        def get_by_role(self,role,*,name,exact):
            if name=='确认无风险，继续提交' and state['step']=='warning':return Collection([Control(name)])
            if name=='提交':return Collection([Control(name)])
            return Collection([])
    async def no_sleep(*a):pass
    async def errors(*a):return []
    async def values(*a):return {'sku':'283.99'}
    monkeypatch.setattr(s.asyncio,'sleep',no_sleep)
    monkeypatch.setattr(s,'visible_form_errors',errors)
    monkeypatch.setattr(s,'current_values',values)
    monkeypatch.setattr(m,'pending_seed',lambda *a:None)
    await s.submit_once(Page(),SimpleNamespace(_pace=no_sleep),tmp_path,{'sku':'289.51'},{'sku':'283.99'},bound_target=record,scope_validator=lambda:None,authorization=m.c.AUTH,pending_final=pending)
    assert calls==(['确认无风险，继续提交'] if pending else ['提交','确认无风险，继续提交'])
    assert (tmp_path/'price-risk-confirmation.json').exists()
    assert (tmp_path/'final-submit-claimed.json').exists() is (not pending)
    if not pending:assert state['reads']>=2

@pytest.mark.asyncio
@pytest.mark.parametrize('item',list(m.SUCCESSES)+[m.LIFT])
async def test_saved_items_and_pending_lift_cannot_enter_new_submit(monkeypatch,tmp_path,item):
    with pytest.raises(m.original.SurfaceError,match='not_authorized'):
        await m.amend_original(None,dict(item=item),[],tmp_path/item,lambda:None)
    assert not (tmp_path/item).exists()

def test_pending_seed_requires_original_lift_folder():
    with pytest.raises(m.original.SurfaceError,match='exact_original'):
        m.pending_seed(Path('other'),dict(item=m.LIFT),{})

def setup_apply(monkeypatch,tmp_path,mode=None):
    monkeypatch.setattr(m,'ROOT',tmp_path)
    request={'records':[dict(item=i) for i in (m.STONE,m.LIGHT,m.LIFT)]}
    groups={i:[dict(item=i,action='original_activity_price')] for i in (m.STONE,m.LIGHT,m.LIFT)}
    plan=dict(decisions=[],exceptions=[{'small-table':True}]);calls=[];exports=[]
    monkeypatch.setattr(m,'checked',lambda *a:(request,plan,[{'checkpoint':'22:15'}],{},groups))
    monkeypatch.setattr(m,'accepted',lambda *a:None)
    m.c.save(tmp_path/'binding.json',{})
    async def amend(session,record,changes,folder,validator):
        calls.append(record['item'])
        if record['item']==m.LIGHT and mode=='before':raise RuntimeError('before_write_failure')
        m.c.save(folder/'write-intent.json',{'unknown':True})
        if record['item']==m.LIGHT and mode=='unknown':raise RuntimeError('after_write_unknown')
        return dict(state='saved_prices_verified')
    async def lift(session,record,folder,validator):
        return await amend(session,record,[],folder,validator)
    async def export(*args):exports.append('new-final');return {'new':True},[]
    def audit(before,*args):
        assert before==[{'checkpoint':'22:15'}]
        return dict(all_verified=True,all_final_prices_known=True)
    monkeypatch.setattr(m,'amend_original',amend);monkeypatch.setattr(m,'continue_lift',lift)
    monkeypatch.setattr(m.original,'final_export',export);monkeypatch.setattr(m.c,'audit',audit)
    return SimpleNamespace(lock=asyncio.Lock()),calls,exports

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',[None,'before','unknown'])
async def test_stone_first_and_independent_prewrite_failure_does_not_block_lift(monkeypatch,tmp_path,mode):
    session,calls,exports=setup_apply(monkeypatch,tmp_path,mode)
    result=await m.apply(session,'sha')
    assert calls==([m.STONE,m.LIGHT] if mode=='unknown' else [m.STONE,m.LIGHT,m.LIFT])
    assert result['inherited_completed_items']==list(m.SUCCESSES) and exports==['new-final']
    assert result['state']==('stopped_unknown_no_replay' if mode=='unknown' else 'finished_with_failures' if mode=='before' else 'resolved_with_small_table_exceptions')

@pytest.mark.asyncio
async def test_unknown_claim_never_dispatches_again(monkeypatch,tmp_path):
    monkeypatch.setattr(m,'ROOT',tmp_path);monkeypatch.setattr(m.c,'validate_request',lambda *a,**kw:{})
    m.c.save(tmp_path/'apply/intent.json',dict(original=True))
    r=await m.handle(None,'program_nov88_anomaly_remaining_apply',{'request_sha256':'sha'})
    assert r['state']=='already_started_no_replay'

@pytest.mark.asyncio
async def test_chunk_reload_is_bounded_and_forbidden_after_write(monkeypatch,tmp_path):
    class Body:
        async def inner_text(self,**kw):return '远程模块加载失败 Loading chunk 8025 failed'
    class Empty:
        def filter(self,**kw):return self
        async def count(self):return 0
    class Page:
        url='https://myseller.taobao.com/mkt-tools-reduce/home'
        reloads=0
        def locator(self,x):return Body() if x=='body' else Empty()
        async def reload(self,**kw):self.reloads+=1
    async def nothing(*a):pass
    monkeypatch.setattr(m.DiscountPage,'guard',nothing);monkeypatch.setattr(m.asyncio,'sleep',nothing)
    p=Page();d=m.ReadyDiscount(p,shop='畔色木作',**m.c.WINDOW,evidence=tmp_path)
    await d.guard();assert p.reloads==1
    with pytest.raises(m.original.SurfaceError,match='no_repeat'):await d.guard()
    p=Page();d=m.ReadyDiscount(p,shop='畔色木作',**m.c.WINDOW,evidence=tmp_path/'second');d.attempted_items.add(m.LIGHT)
    with pytest.raises(m.original.SurfaceError,match='no_repeat'):await d.guard()
    assert p.reloads==0

@pytest.mark.asyncio
@pytest.mark.parametrize('operation,module_name',[('program_nov88_anomaly_resume_status','repair_resume'),('program_nov88_anomaly_remaining_status','repair_remaining')])
async def test_both_fixed_host_routes_keep_single_module_argument(monkeypatch,operation,module_name):
    path=Path(__file__).parent/'campaign_nov88_anomaly.py'
    spec=importlib.util.spec_from_file_location('test_nov88_remaining_host',path)
    host=importlib.util.module_from_spec(spec);spec.loader.exec_module(host)
    loaded=[]
    async def handle(session,op,options):return {'ok':True,'op':op}
    def loader(name):loaded.append(name);return SimpleNamespace(handle=handle)
    monkeypatch.setattr(host,'module',loader)
    result=await host.handle(None,operation,{})
    assert result['ok'] and loaded==[module_name]
