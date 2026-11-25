import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def harness(monkeypatch, tmp_path):
    path=Path(__file__).resolve().parents[2]/'scripts/campaign_pipeline_erp_runner.py'
    spec=importlib.util.spec_from_file_location('pipeline_runner_test',path)
    runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
    events=[]
    bundle=dict(campaign_id='123',united_activity_id='456',sign_record_id='789',
        shop='畔色木作',campaign_start='2026-11-26',campaign_end='2026-11-28',
        discount_rate='0.12',campaign_tier='big_target',price_version='a'*64,
        single_discount_activity_id='',all_erp_rows=[{}],execution_ready=True,live_enabled=True)
    claim={'attempt_id':'b'*24,'request_id':'c'*32,'bundle':bundle}
    receipts=[]
    def api(base,path,token,payload=None):
        events.append(path)
        if '/pipeline-receipts/' in path:receipts.append(payload);return {'recorded':True}
        if '/pipeline-claim' in path:return claim
        return bundle
    def edge(op,opts,**kwargs):
        events.append(op)
        assert opts['work_dir'].endswith(claim['attempt_id'])
        if op=='pipeline_status':return {'status':{'state':'done','attempt_id':'local1'}}
        return {'ok':True}
    cli=SimpleNamespace(load_edge_token=lambda:'test-edge',call_edge=edge,
        DEFAULT_EDGE_URL='http://localhost',poll_until_done=lambda *a,**k:{'state':'done','attempt_id':'local1'})
    calc=SimpleNamespace(CampaignConfig=SimpleNamespace(from_options=lambda *a,**k:None),
        validate_erp_against_config=lambda *a:None,load_erp_bundle=lambda p:None)
    monkeypatch.setenv('PANSE_API_TOKEN','test-operator')
    monkeypatch.setattr(runner,'validate_runtime',lambda p:None)
    monkeypatch.setattr(runner,'load_module',lambda path,name:cli if 'cli' in name else calc)
    monkeypatch.setattr(runner,'api',api)
    def run(*args):
        monkeypatch.setattr(runner.sys,'argv',['runner','--plan-id','1','--output',str(tmp_path),*args])
        return runner.main()
    return runner,cli,claim,events,receipts,run,tmp_path


def test_execute_claims_before_single_dispatch(harness):
    runner,cli,claim,events,receipts,run,output=harness
    assert run('--execute')==0
    assert events.index('/api/campaigns/1/pipeline-claim') < events.index('pipeline_enroll')
    assert events.count('pipeline_enroll')==1
    assert receipts[0]['state']=='done'
    assert 'test-operator' not in ''.join(p.read_text() for p in output.rglob('*.json'))


def test_timeout_never_replays_and_can_collect_original(harness):
    runner,cli,claim,events,receipts,run,output=harness
    def timeout(*a,**kw):raise TimeoutError('timeout')
    cli.call_edge=timeout
    assert run('--execute')==2
    assert receipts[0]['state']=='unknown_pending_verify'
    claim['receipt']=receipts[0]
    cli.call_edge=lambda op,*a,**k: events.append(op) or {'status':{'state':'done','attempt_id':'local1'}}
    assert run('--recover',claim['attempt_id'])==0
    assert events.count('/api/campaigns/1/pipeline-claim')==1
    assert events.count('pipeline_status')==1
    assert 'pipeline_enroll' not in events
    assert receipts[-1]['state']=='done'


def test_default_validation_does_not_claim_or_dispatch(harness):
    *_,events,receipts,run,output=harness
    assert run()==0
    assert events==['/api/campaigns/1/pipeline-bundle']
    assert not receipts


def test_claim_failure_never_reaches_browser(harness,monkeypatch):
    runner,cli,claim,events,receipts,run,output=harness
    original=runner.api
    def api(base,path,*args):
        if path.endswith('pipeline-claim'):raise TimeoutError('claim response lost')
        return original(base,path,*args)
    monkeypatch.setattr(runner,'api',api)
    with pytest.raises(TimeoutError):run('--execute')
    assert 'pipeline_enroll' not in events
    assert not receipts
