"""Regression for observed false successes and replay; entirely offline."""
import hashlib
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from fulfillment_batch_runner import run_fulfillment, TaobaoConsignAutomation
import fulfillment_batch_runner as runner
from app.services.fulfillment_receipts import reconcile

ROWS=[{'order_no':'A','shop':'test','carrier':'中通快运','tracking_no':'TEST001','sub_order_nos':['a1']},
      {'order_no':'B','shop':'test','carrier':'中通快运','tracking_no':'TEST002','sub_order_nos':['b1']}]
TASK={'taskId':'exact','taskStatus':'FINISHED','successCount':2,'failCount':0}

def proof(row):
    return {**row,'source':'taobao_logistics_readback','seller_confirm':True,
            'trade_status':'WAIT_BUYER_CONFIRM_GOODS','evidence_ref':'test-only',
            'observed_at':datetime.now(timezone.utc).isoformat()}

def test_missing_error_details_cannot_mark_all_success():
    results,state=reconcile(ROWS,{**TASK,'taskStatus':'PART_FAILED','successCount':1,'failCount':1},
        {'error':'unavailable'},expected_task_id='exact')
    assert state=='unknown' and all(v['status']=='unknown' for v in results.values())

def test_real_contract_success_requires_each_order_proof():
    assert reconcile(ROWS,TASK,{},[proof(r) for r in ROWS],expected_task_id='exact')[1]=='confirmed'

@pytest.mark.parametrize('change',['task','count','shop','tracking','children','duplicate','future','old'])
def test_proof_mismatch(change):
    task=dict(TASK); proofs=[proof(r) for r in ROWS]; start=None
    if change=='task': task['taskId']='other'
    if change=='count': task['successCount']=1
    if change=='shop': proofs[0]['shop']='other'
    if change=='tracking': proofs[0]['tracking_no']='other'
    if change=='children': proofs[0]['sub_order_nos']=[]
    if change=='duplicate': proofs.append(dict(proofs[0]))
    if change=='future': proofs[0]['observed_at']=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
    if change=='old': start=(datetime.now(timezone.utc)+timedelta(seconds=1)).isoformat()
    results,state=reconcile(ROWS,task,{},proofs,expected_task_id='exact',started_at=start)
    assert results['A']['status']=='unknown' and state!='confirmed'

@pytest.mark.asyncio
async def test_exact_task_only_no_recent_fallback():
    tb=TaobaoConsignAutomation('not-connected')
    tb.get_task_list=AsyncMock(return_value=[{'taskId':'other','taskCode':'consign2_batch_offline_consign','createTime':10**20}])
    assert await tb.find_existing_task('exact') is None
    assert await tb.find_existing_task() is None
    assert tb.get_task_list.await_count==1

@pytest.mark.asyncio
async def test_direct_dry_run_never_attaches_file():
    tb=TaobaoConsignAutomation('not-connected'); tb.page=MagicMock()
    assert await tb.upload_and_submit('nonexistent',True)=='DRY_RUN_NO_UPLOAD'
    tb.page.locator.assert_not_called()

@pytest.mark.parametrize('failure',['shop','start_ack','upload_ack','unaccepted','no_task'])
@pytest.mark.asyncio
async def test_no_resubmission_after_unknown_or_missing_identity(failure):
    erp=MagicMock(); content=b'fixture'
    erp.get_batch.return_value={'id':'test','state':'submitting' if failure=='no_task' else 'prepared',
        'shop':'test','sha256':hashlib.sha256(content).hexdigest(),'rows':ROWS}
    erp.download_batch_file.return_value=content
    erp.record_start.return_value={'upload_allowed':True,'execution':{'started_at':'2026-01-01T00:00:00+00:00'}}
    tb=AsyncMock(); tb.inspect_shop.return_value={'userid':'seller','shopName':'' if failure=='shop' else 'test'}
    if failure=='start_ack': erp.record_start.side_effect=TimeoutError('lost')
    if failure=='upload_ack': tb.upload_and_submit.side_effect=TimeoutError('lost')
    with patch.object(runner,'ErpClient',return_value=erp), patch.object(runner,'TaobaoConsignAutomation') as factory:
        factory.live_adapter_verified=failure!='unaccepted'; factory.return_value=tb
        with pytest.raises((RuntimeError,TimeoutError)):
            await run_fulfillment(batch_id='test',erp_token='offline',cdp_url='mock-only')
    if failure!='upload_ack': tb.upload_and_submit.assert_not_called()
    erp.record_submission.assert_not_called(); erp.record_finish.assert_not_called()

def test_no_credential_minting_or_browser_scanning(monkeypatch):
    monkeypatch.delenv('PANSE_API_TOKEN',raising=False); monkeypatch.delenv('PANSE_SHIPPING_CDP_URL',raising=False)
    with patch.object(runner.urllib.request,'urlopen',side_effect=AssertionError('network forbidden')):
        assert runner.fetch_admin_token('unused') is None
        assert runner.discover_edge_cdp() is None
