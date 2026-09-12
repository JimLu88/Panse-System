import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from campaign_continuous_flow import Store
from campaign_continuous_policy import fingerprint
from campaign_price_snapshot import digest
from campaign_submission_gate import source_version_matches
from campaign_unclaimed_recovery import recover


@pytest.mark.parametrize('bad',[None,'related','baseline','changed_prior','empty'])
def test_only_unchanged_prefix_plus_unrelated_mapping_is_accepted(bad):
    original=[{'path':'old','kind':'fixed','sha256':'original','document':{}}]
    body={'entry_source_sha256':digest([{k:original[0][k] for k in ('path','kind','sha256')}]),
          'signup_items':['1'],'discount_items':['1']}
    extra={'path':'new','kind':'mapping','sha256':'new','document':
        {'status':'verified_partial_mapping_restored','restored':[{'item':'2'}]}}
    if bad=='related':extra['document']['restored'][0]['item']='1'
    if bad=='baseline':extra['kind']='fixed'
    if bad=='changed_prior':original[0]['sha256']='changed'
    if bad=='empty':extra['document']['restored']=[]
    assert source_version_matches(SimpleNamespace(sources=lambda:original+[extra]),body,'different') is (bad is None)


@pytest.mark.parametrize('bad',[None,'claim','artifact','pending','success','owner','validation'])
def test_preclaim_continuation_never_releases_existing_claim(tmp_path,bad):
    store=Store(tmp_path/'controller.sqlite3');run=store.start('test','rule')
    payload={'items':['1'],'bundle':{'bundle_id':'bundle'}};sha=fingerprint(payload)
    action=fingerprint([run,'signup',sha]);state=store.load(run)
    state.update(status='blocked',pending=['2'] if bad=='pending' else ['1'])
    if bad=='success':state['success']={'1':'official'}
    store.save(run,state)
    if bad=='owner':store.db.execute('UPDATE continuous_campaign_runs SET owner=?',('active',))
    store.db.execute('INSERT INTO continuous_campaign_actions VALUES(?,?,?,?,?,NULL)',(action,run,'signup',sha,'unknown'))
    folder=tmp_path/'segments'/'segment'/'actions'/action;folder.mkdir(parents=True)
    (folder/'request.json').write_text(json.dumps({'step':'signup','payload':payload}),encoding='utf-8')
    if bad=='artifact':(folder/'claim.json').write_text('{}')
    authority=SimpleNamespace(db=sqlite3.connect(':memory:'))
    authority.db.execute('CREATE TABLE attempts(bundle_id TEXT,phase TEXT)')
    if bad=='claim':authority.db.execute('INSERT INTO attempts VALUES(?,?)',('bundle','signup'))
    authority_changes=authority.db.total_changes
    with patch('campaign_unclaimed_recovery.validated_body',return_value=({'signup_rows':[{'item':'1'}]},{}),
               side_effect=ValueError('invalid_file') if bad=='validation' else None):
        if bad:
            with pytest.raises(ValueError):recover(tmp_path,store,authority)
            assert store.db.execute('SELECT status FROM continuous_campaign_actions').fetchone()[0]=='unknown'
        else:
            assert recover(tmp_path,store,authority)==action
            assert store.db.execute('SELECT COUNT(*) FROM continuous_campaign_actions').fetchone()[0]==0
            assert store.db.execute('SELECT action_id FROM continuous_preclaim_recoveries').fetchone()[0]==action
            with pytest.raises(ValueError):recover(tmp_path,store,authority)
    assert authority.db.total_changes==authority_changes
    authority.db.close();store.close()
