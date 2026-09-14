import sys
import pytest
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import campaign_owned_execution as mod


def test_checkpoint_requires_idle_unique_exact_reference(tmp_path):
    import sqlite3,json
    db=sqlite3.connect(tmp_path/'controller.sqlite3')
    db.executescript('CREATE TABLE continuous_campaign_runs(id TEXT,owner TEXT);'
        'CREATE TABLE continuous_campaign_actions(id TEXT,run_id TEXT,step TEXT,status TEXT);'
        "INSERT INTO continuous_campaign_runs VALUES('r',NULL);"
        "INSERT INTO continuous_campaign_actions VALUES('a','r','signup','unknown');")
    db.commit()
    folder=tmp_path/'segments'/'s'/'actions'/'a';folder.mkdir(parents=True)
    ref=folder/'signup-job.json';ref.write_text(json.dumps(dict(step='signup',job_id='j')))
    assert mod.checkpoint(tmp_path)['job_id']=='j'
    db.execute("UPDATE continuous_campaign_runs SET owner='active'");db.commit()
    assert mod.checkpoint(tmp_path) is None
    db.execute('UPDATE continuous_campaign_runs SET owner=NULL');db.commit()
    ref.write_text(json.dumps(dict(step='discount',job_id='j')))
    assert mod.checkpoint(tmp_path) is None
    db.close()


def test_changed_job_identity_never_reconciles(tmp_path,monkeypatch):
    point=dict(action_id='a'*64,step='signup',operation='signup',job_id='b'*64)
    monkeypatch.setattr(mod,'checkpoint',lambda _:point)
    rec=Mock();monkeypatch.setattr(mod,'reconcile',rec)
    edge=Mock();edge.status.return_value=dict(job_id='other',operation='signup',state='finished')
    result=mod.execute_owned({},root=tmp_path,authority=None,edge=edge,artifact_roots=[],
        execute=Mock(return_value=dict(status='blocked')))
    assert result['owned_reconciliation']['automatic_retry'] is False
    rec.assert_not_called();edge.submit.assert_not_called()


def test_completed_run_never_checks_or_replays_jobs(tmp_path,monkeypatch):
    monkeypatch.setattr(mod,'checkpoint',Mock(side_effect=AssertionError('unexpected')))
    edge=Mock();execute=Mock(return_value=dict(status='complete',all_signed_up=False,segments=[]))
    assert mod.execute_owned({},root=tmp_path,authority=None,edge=edge,artifact_roots=[],execute=execute)['status']=='complete'
    edge.status.assert_not_called();execute.assert_called_once()


def test_original_running_job_reconciles_without_submit_then_finishes(tmp_path,monkeypatch):
    point=dict(action_id='a'*64,step='signup',operation='signup',job_id='b'*64)
    monkeypatch.setattr(mod,'checkpoint',lambda _:point)
    rec=Mock(return_value=dict(platform_write=False));monkeypatch.setattr(mod,'reconcile',rec)
    edge=Mock();edge.status.return_value=dict(job_id='b'*64,operation='signup',state='running')
    edge.wait.return_value=dict(job_id='b'*64,operation='signup',state='finished')
    execute=Mock(side_effect=[dict(status='blocked'),dict(status='complete')])
    result=mod.execute_owned({},root=tmp_path,authority=None,edge=edge,artifact_roots=[],execute=execute)
    assert result['status']=='complete' and execute.call_count==2
    rec.assert_called_once();edge.submit.assert_not_called()
    assert (tmp_path/'owned-reconciliation'/('a'*64+'.json')).exists()


def test_unknown_and_repeated_checkpoint_do_not_loop(tmp_path,monkeypatch):
    point=dict(action_id='a'*64,step='signup',operation='signup',job_id='b'*64)
    monkeypatch.setattr(mod,'checkpoint',lambda _:point)
    rec=Mock(return_value={});monkeypatch.setattr(mod,'reconcile',rec)
    edge=Mock();edge.status.return_value=dict(job_id='b'*64,operation='signup',state='unknown')
    execute=Mock(return_value=dict(status='blocked',all_signed_up=False,segments=[]))
    assert mod.execute_owned({},root=tmp_path,authority=None,edge=edge,artifact_roots=[],execute=execute)['status']=='blocked'
    execute.assert_called_once();rec.assert_not_called();edge.wait.assert_not_called();edge.submit.assert_not_called()
    edge.status.return_value=dict(job_id='b'*64,operation='signup',state='finished')
    execute.reset_mock()
    mod.execute_owned({},root=tmp_path,authority=None,edge=edge,artifact_roots=[],execute=execute)
    assert execute.call_count==2 and rec.call_count==1


@pytest.mark.parametrize('proven',[True,False])
def test_owned_before_binding_continuation_uses_original_guarded_job_once(tmp_path,monkeypatch,proven):
    point=dict(action_id='a'*64,step='signup',operation='signup',job_id='b'*64)
    monkeypatch.setattr(mod,'checkpoint',lambda _:point)
    rec=Mock(return_value={});monkeypatch.setattr(mod,'reconcile',rec)
    failure=dict(reason='legacy_campaign_page_not_unique',recording=dict(state='unavailable_before_page_binding'),
        program_location=[dict(file='campaign_bound_transfers.py',function='page')])
    if not proven:failure['program_location']=[]
    first=dict(job_id='b'*64,operation='signup',state='unknown',result=failure)
    edge=Mock();edge.status.side_effect=[first,dict(job_id='b'*64,operation='signup',state='running')]
    edge.wait.return_value=dict(job_id='b'*64,operation='signup',state='finished')
    execute=Mock(side_effect=[dict(status='blocked'),dict(status='complete')])
    result=mod.execute_owned({},root=tmp_path,authority=None,edge=edge,artifact_roots=[],execute=execute)
    if proven:
        assert result['status']=='complete'
        edge._action.assert_called_once_with('program_resume_signup_before_page_binding',{'job_id':'b'*64})
        rec.assert_called_once()
    else:
        assert result['status']=='blocked';edge._action.assert_not_called();rec.assert_not_called()
    edge.submit.assert_not_called()
