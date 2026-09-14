import json
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import campaign_continuous_recovery as recovery
from campaign_continuous_flow import Store
from campaign_continuous_policy import fingerprint
from campaign_continuous_transport import persist


def test_latest_outcome_keeps_every_previous_summary(tmp_path):
    before={'status':'blocked','source':'original'};after={'status':'complete','source':'new receipt'}
    persist(tmp_path/'result.json',before)
    recovery.persist_run_outcome(tmp_path,after)
    recovery.persist_run_outcome(tmp_path,after)
    assert json.loads((tmp_path/'result.json').read_text())==after
    assert {p.stem for p in (tmp_path/'result-history').glob('*.json')}=={fingerprint(before),fingerprint(after)}


@pytest.mark.parametrize('bad',[None,'running_job','wrong_job','active_owner','wrong_payload'])
@pytest.mark.parametrize('phase',['discount','signup'])
def test_discount_recovery_only_reads_original_finished_job(tmp_path,monkeypatch,bad,phase):
    payload={'bundle':{'bundle_id':'fixture'}};sha=fingerprint(payload);action='a'*64;claim='b'*32
    jid=fingerprint([phase,claim]);store=Store(tmp_path/'controller.sqlite3')
    run=store.start('fixture','rules')
    store.db.execute('INSERT INTO continuous_campaign_actions VALUES(?,?,?,?,?,NULL)',(action,run,phase,sha,'unknown'))
    if bad=='active_owner':store.db.execute("UPDATE continuous_campaign_runs SET owner='active'")
    store.close()
    folder=tmp_path/'segments'/'segment'/'actions'/action
    persist(folder/'request.json',{'step':phase,'payload':{} if bad=='wrong_payload' else payload})
    persist(folder/'claim.json',{'claim_id':claim})
    persist(folder/(phase+'-job.json'),{'step':phase,'job_id':jid})
    edge=Mock();edge.status.return_value={'job_id':jid if bad!='wrong_job' else 'c'*64,
        'operation':phase,'state':'running' if bad=='running_job' else 'finished',
        'result':{'claim':{'claim_id':claim}}}
    normalized=Mock(return_value={'status':'terminal','batch':'123','items':[],'platform_write':False})
    monkeypatch.setattr(recovery,'reconcile_'+phase,normalized)
    recover=getattr(recovery,'recover_finished_'+phase)
    if bad:
        with pytest.raises(ValueError):recover(tmp_path,object(),edge)
        normalized.assert_not_called()
    else:
        result=recover(tmp_path,object(),edge)
        assert result['job_id']==jid and result['platform_write'] is False
        check=Store(tmp_path/'controller.sqlite3')
        row=check.db.execute('SELECT status,result FROM continuous_campaign_actions').fetchone()
        assert row[0]=='done' and json.loads(row[1])['reconciled_readonly'] is True
        check.close()
        assert (folder/('reconciled-'+phase+'-observation.json')).exists()
    edge.submit.assert_not_called();edge._action.assert_not_called()


@pytest.mark.parametrize('bad',[None,'running_job','wrong_job','active_owner','wrong_payload','wrong_step','invalid_export'])
def test_scope_recovery_reuses_original_receipt_without_browser_actions(tmp_path,monkeypatch,bad):
    from campaign_continuous_transport import CampaignTransport
    payload={'identity':{'shop_id':'测试店'},'rule_sha':'rules'}
    action='d'*64;sha=fingerprint(payload);jid=fingerprint(['product_export','测试店',action])
    store=Store(tmp_path/'controller.sqlite3');run=store.start('fixture','rules')
    store.db.execute('INSERT INTO continuous_campaign_actions VALUES(?,?,?,?,?,NULL)',
        (action,run,'signup' if bad=='wrong_step' else 'scope',sha,'interrupted_read'))
    if bad=='active_owner':store.db.execute("UPDATE continuous_campaign_runs SET owner='active'")
    store.close()
    folder=tmp_path/'shared'/'actions'/action
    persist(folder/'request.json',{'step':'scope','payload':{} if bad=='wrong_payload' else payload})
    persist(folder/'product_export-job.json',{'step':'product_export','job_id':jid})
    previous={'state':'unknown','error':'TimeoutError'}
    persist(folder/'product_export-observation.json',previous)
    persist(tmp_path/'request.json',{'original':True})
    persist(tmp_path/'shared'/'snapshot.json',{'version':'original'})
    edge=Mock();edge.status.return_value={'job_id':jid if bad!='wrong_job' else 'e'*64,
        'operation':'product_export','state':'running' if bad=='running_job' else 'finished'}
    normalize=Mock(side_effect=ValueError('bad file hash')) if bad=='invalid_export' else Mock(return_value={
        'complete':True,'erp_sellable':['1'],'price_version':'fixed','platform_rows':[{'item':'1'}]})
    monkeypatch.setattr(CampaignTransport,'finish_exported_scope',normalize)
    if bad:
        with pytest.raises(ValueError):recovery.recover_finished_scope(tmp_path,object(),edge,artifact_roots=[tmp_path])
    else:
        result=recovery.recover_finished_scope(tmp_path,object(),edge,artifact_roots=[tmp_path])
        assert result['job_id']==jid
        check=Store(tmp_path/'controller.sqlite3')
        row=check.db.execute('SELECT status,result FROM continuous_campaign_actions').fetchone()
        assert row[0]=='done' and json.loads(row[1])['reconciled_readonly'] is True
        check.close()
        assert normalize.call_args.args[0]=={'version':'original'}
        assert normalize.call_args.args[2]==action
    assert json.loads((folder/'product_export-observation.json').read_text())==previous
    edge.submit.assert_not_called();edge._action.assert_not_called()
