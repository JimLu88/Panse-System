import json
import sqlite3
import pytest
from campaign_report_checkpoint import validate
from campaign_continuous_policy import fingerprint


def sample(root):
    db=sqlite3.connect(':memory:')
    db.executescript('CREATE TABLE continuous_campaign_actions(id,run_id,step,status,payload_sha); CREATE TABLE continuous_campaign_runs(id,owner,body); CREATE TABLE continuous_campaign_diagnostics(action_id,code,event_id);')
    p=dict(batch='b',failed_phase='discount',items=['c'])
    folder=root/'segments/s/actions/a';folder.mkdir(parents=True)
    (folder/'request.json').write_text(json.dumps(dict(step='report',payload=p)))
    segment=folder.parent.parent
    for name,value in [('terminals/discount-b.json',dict(batch='b',bundle_id='x')),
                       ('reports/b.json',dict(batch='b',bundle_id='x',errors=[dict(item='a')]))]:
        target=segment/name;target.parent.mkdir(exist_ok=True);target.write_text(json.dumps(value))
    state=dict(blocker=dict(step='report',reason='continuous_evidence_is_immutable'))
    db.execute('INSERT INTO continuous_campaign_runs VALUES(?,?,?)',('r',None,json.dumps(state)))
    db.execute('INSERT INTO continuous_campaign_actions VALUES(?,?,?,?,?)',('a','r','report','interrupted_read',fingerprint(p)))
    db.execute('INSERT INTO continuous_campaign_diagnostics VALUES(?,?,?)',('a','continuous_evidence_is_immutable',1))
    return db,folder


def test_exact_read_checkpoint(tmp_path):
    db,_=sample(tmp_path)
    proof=validate(db,tmp_path)
    assert proof['action_id']=='a' and proof['platform_write'] is False
    assert db.execute('SELECT count(*) FROM continuous_campaign_actions').fetchone()[0]==1


@pytest.mark.parametrize('sql', ["UPDATE continuous_campaign_actions SET step='signup'",
    "UPDATE continuous_campaign_actions SET status='unknown'",
    "UPDATE continuous_campaign_runs SET owner='active'",
    "UPDATE continuous_campaign_diagnostics SET code='other'",
    "UPDATE continuous_campaign_actions SET payload_sha='changed'"])
def test_refuse_unrelated_checkpoint(tmp_path,sql):
    db,_=sample(tmp_path);db.execute(sql)
    with pytest.raises(ValueError):validate(db,tmp_path)


@pytest.mark.parametrize('name',['claim.json','result.json'])
def test_never_recover_consumed_report(tmp_path,name):
    db,folder=sample(tmp_path);(folder/name).write_text('{}')
    with pytest.raises(ValueError):validate(db,tmp_path)
