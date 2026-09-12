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
def test_discount_recovery_only_reads_original_finished_job(tmp_path,monkeypatch,bad):
    payload={'bundle':{'bundle_id':'fixture'}};sha=fingerprint(payload);action='a'*64;claim='b'*32
    jid=fingerprint(['discount',claim]);store=Store(tmp_path/'controller.sqlite3')
    run=store.start('fixture','rules')
    store.db.execute('INSERT INTO continuous_campaign_actions VALUES(?,?,?,?,?,NULL)',(action,run,'discount',sha,'unknown'))
    if bad=='active_owner':store.db.execute("UPDATE continuous_campaign_runs SET owner='active'")
    store.close()
    folder=tmp_path/'segments'/'segment'/'actions'/action
    persist(folder/'request.json',{'step':'discount','payload':{} if bad=='wrong_payload' else payload})
    persist(folder/'claim.json',{'claim_id':claim})
    persist(folder/'discount-job.json',{'step':'discount','job_id':jid})
    edge=Mock();edge.status.return_value={'job_id':jid if bad!='wrong_job' else 'c'*64,
        'operation':'discount','state':'running' if bad=='running_job' else 'finished',
        'result':{'claim':{'claim_id':claim}}}
    normalized=Mock(return_value={'status':'terminal','batch':'123','items':[],'platform_write':False})
    monkeypatch.setattr(recovery,'reconcile_discount',normalized)
    if bad:
        with pytest.raises(ValueError):recovery.recover_finished_discount(tmp_path,object(),edge)
        normalized.assert_not_called()
    else:
        result=recovery.recover_finished_discount(tmp_path,object(),edge)
        assert result['job_id']==jid and result['platform_write'] is False
        check=Store(tmp_path/'controller.sqlite3')
        row=check.db.execute('SELECT status,result FROM continuous_campaign_actions').fetchone()
        assert row[0]=='done' and json.loads(row[1])['reconciled_readonly'] is True
        check.close()
        assert (folder/'reconciled-discount-observation.json').exists()
    edge.submit.assert_not_called();edge._action.assert_not_called()
