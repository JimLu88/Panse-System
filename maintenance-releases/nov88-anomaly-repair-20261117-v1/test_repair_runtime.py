import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
sys.path.insert(0,r'D:/AI/畔色ERP系统/Web-Agent程序')
import repair_runtime as m
from app.engine import campaign_completion_submit as submission

class Page:
    def __init__(self):self.closed=False
    async def close(self):self.closed=True
class Recorder:
    def __init__(self,*a,**k):pass
    async def start(self):pass
    async def stop(self):return {'test':True}

def setup(monkeypatch,tmp_path,*,mismatch=False,reject=False):
    state={'a':'1000.00','b':'300.00'};submits=[];pages=[]
    record=dict(item='111111111',marketing_id='222222222',rows=[dict(sku=s,activity_price=v) for s,v in state.items()])
    def observed():return {'rows':[dict(sku_id=s,price=v,other_inputs=[{'type':'checkbox','checked':True,'value':'on'}]) for s,v in state.items()]}
    async def pace(*args):pass
    async def open_editor(session,record,folder):
        folder.mkdir(parents=True);p=Page();pages.append(p)
        data=observed()
        if mismatch:data['rows'][0]['price']='999.00'
        return p,SimpleNamespace(_pace=pace),data,'异常'
    async def inventory(page,expected):return observed()
    class Field:
        def __init__(self,s):self.s=s
        async def fill(self,value,**kw):state[self.s]=value
        async def press(self,*a):pass
    async def field(page,sku):return Field(sku)
    async def submit(page,io,folder,before,after,**kw):
        submits.append((before,after))
        if reject:raise m.SurfaceError(reject if isinstance(reject,str) else 'unconfirmed_submit_result')
    monkeypatch.setattr(m,'open_editor',open_editor);monkeypatch.setattr(m,'inventory',inventory)
    monkeypatch.setattr(m,'exact_field',field);monkeypatch.setattr(m,'RetainedCampaignRecorder',Recorder)
    monkeypatch.setattr(submission,'submit_once',submit)
    return record,state,submits,pages

@pytest.mark.asyncio
async def test_original_save_once_and_other_fields_preserved(monkeypatch,tmp_path):
    record,state,submits,pages=setup(monkeypatch,tmp_path)
    folder=tmp_path/'item'
    await m.amend_original(None,record,[dict(sku='a',action='original_activity_price',new_price='500.00')],folder,lambda:None)
    assert len(submits)==1 and state=={'a':'500.00','b':'300.00'}
    assert (folder/'terminal.json').exists() and all(p.closed for p in pages)
    with pytest.raises(FileExistsError):
        await m.amend_original(None,record,[],folder,lambda:None)
    assert len(submits)==1

@pytest.mark.asyncio
async def test_changed_original_price_stops_before_fill_or_submit(monkeypatch,tmp_path):
    record,state,submits,pages=setup(monkeypatch,tmp_path,mismatch=True)
    with pytest.raises(m.SurfaceError,match='baseline_changed'):
        await m.amend_original(None,record,[dict(sku='a',action='original_activity_price',new_price='500.00')],tmp_path/'item',lambda:None)
    assert not submits and state['a']=='1000.00'
    assert not list(tmp_path.rglob('write-intent.json'))

@pytest.mark.asyncio
async def test_submission_failure_keeps_intent_no_success_receipt(monkeypatch,tmp_path):
    record,state,submits,pages=setup(monkeypatch,tmp_path,reject=True)
    with pytest.raises(m.SurfaceError,match='unconfirmed'):
        await m.amend_original(None,record,[dict(sku='a',action='original_activity_price',new_price='500.00')],tmp_path/'item',lambda:None)
    assert len(submits)==1 and (tmp_path/'item/write-intent.json').exists()
    assert not (tmp_path/'item/terminal.json').exists() and not pages[0].closed

@pytest.mark.asyncio
async def test_explicit_official_rejection_can_isolate_item(monkeypatch,tmp_path):
    record,state,submits,pages=setup(monkeypatch,tmp_path,reject='official_sku_price_validation_rejected')
    result=await m.amend_original(None,record,[dict(sku='a',action='original_activity_price',new_price='500.00')],tmp_path/'item',lambda:None)
    assert result['state']=='official_rejected' and len(submits)==1
    assert not result['saved_prices_verified'] and pages[0].closed
