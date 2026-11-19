import copy
import json
import sqlite3
from pathlib import Path

import pytest
import campaign_scope_checkpoint as checkpoint
import campaign_scope_maintenance as maintenance
import campaign_execution_release as release
from campaign_continuous_policy import fingerprint
from campaign_entry_authority import file_sha


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')
    return {'path': str(path), 'sha256': file_sha(path)}


def fixture(tmp_path, monkeypatch):
    request={'pages':{'super':{'shop_id':'shop'}}}
    rid=fingerprint(request);root=tmp_path/'runs'/rid;root.mkdir(parents=True)
    write(root/'request.json',request);write(tmp_path/'requests'/(rid+'.json'),request)
    write(root/'shared/snapshot.json',{'prices':'unchanged'})
    (root/'process.log').write_text('original failed read')
    payload={'identity':request['pages']['super']};action='a'*64
    folder=root/'shared/actions'/action
    write(folder/'request.json',dict(step='scope',payload=payload))
    job_id=fingerprint(['product_export','shop',action])
    write(folder/'product_export-job.json',dict(step='product_export',job_id=job_id))
    write(folder/'product_export-observation.json',{'original':True})
    with sqlite3.connect(tmp_path/'jobs.sqlite3') as db:
        db.execute('CREATE TABLE continuous_jobs(id TEXT,state TEXT,result TEXT)')
        db.execute('INSERT INTO continuous_jobs VALUES(?,?,?)',(rid,'blocked',json.dumps({'blocker':{'step':'scope'}})))
    with sqlite3.connect(root/'controller.sqlite3') as db:
        db.execute('CREATE TABLE continuous_campaign_runs(id TEXT,owner TEXT)')
        db.execute('CREATE TABLE continuous_campaign_actions(id TEXT,step TEXT,status TEXT,run_id TEXT,payload_sha TEXT)')
        db.execute("INSERT INTO continuous_campaign_runs VALUES('run',NULL)")
        db.execute('INSERT INTO continuous_campaign_actions VALUES(?,?,?,?,?)',(action,'scope','interrupted_read','run',fingerprint(payload)))
    import campaign_continuous_execute, campaign_product_scope
    from app.engine.campaign_transfer_jobs import TransferJobs
    monkeypatch.setattr(campaign_continuous_execute,'validate_request',lambda r:None)
    job=dict(state='finished',operation='product_export',job_id=job_id,result={'original':True})
    monkeypatch.setattr(TransferJobs,'persisted_status',lambda *a:copy.deepcopy(job))
    monkeypatch.setattr(campaign_product_scope,'from_edge_job',lambda *a,**kw:{'observed_item_count':1,'sku_facts':[1]})
    return root,rid,folder,job


def test_checkpoint_readonly_and_exact(tmp_path,monkeypatch):
    root,rid,folder,job=fixture(tmp_path,monkeypatch)
    before={p:file_sha(p) for p in tmp_path.rglob('*') if p.is_file()}
    proof=checkpoint.verify(root,rid)
    assert proof['sku_count']==1 and proof['export_job_id']==job['job_id']
    assert before=={p:file_sha(p) for p in tmp_path.rglob('*') if p.is_file()}


@pytest.mark.parametrize('case',['request','job','running','owner','done','segments','unfinished','wrong_operation','payload','extra_action'])
def test_checkpoint_rejects_unsafe_states(tmp_path,monkeypatch,case):
    root,rid,folder,job=fixture(tmp_path,monkeypatch)
    if case=='request':write(root/'request.json',{'changed':True})
    elif case=='job':write(folder/'product_export-job.json',dict(step='product_export',job_id='b'*64))
    elif case=='running':
        with sqlite3.connect(tmp_path/'jobs.sqlite3') as db:db.execute("UPDATE continuous_jobs SET state='running'")
    elif case in ('owner','done','extra_action'):
        with sqlite3.connect(root/'controller.sqlite3') as db:
            db.execute({'owner':"UPDATE continuous_campaign_runs SET owner='other'",'done':"UPDATE continuous_campaign_actions SET status='done'",'extra_action':"INSERT INTO continuous_campaign_actions SELECT 'b',step,status,run_id,payload_sha FROM continuous_campaign_actions"}[case])
    elif case=='segments':(root/'segments').mkdir()
    elif case=='unfinished':job['state']='unknown'
    elif case=='wrong_operation':job['operation']='signup'
    elif case=='payload':write(folder/'request.json',dict(step='scope',payload={'changed':True}))
    with pytest.raises(ValueError):checkpoint.verify(root,rid)


def reviewed(tmp_path,monkeypatch):
    root,rid,folder,job=fixture(tmp_path,monkeypatch)
    program=tmp_path/'program';source=program/'scripts/campaign_fixture.py';source.parent.mkdir(parents=True)
    source.write_text('old')
    pin=root/'shared/execution-release.json';first=release.ReleaseGuard(program,pin)
    source.write_text('reviewed new')
    current=release.sources(program);monkeypatch.setattr(release,'sources',lambda root=None:dict(current))
    evidence=write(tmp_path/'tests.json',{'passed':True})
    manifest=dict(request_id=rid,old_release_id=first.release_id,changes=maintenance.differences(first.original,current),validation_evidence=[evidence])
    review=tmp_path/'review.json';write(review,manifest)
    return root,rid,pin,review,current,manifest


def test_explicit_transition_preserves_pin_and_archives(tmp_path,monkeypatch):
    root,rid,pin,review,current,manifest=reviewed(tmp_path,monkeypatch)
    old=pin.read_bytes();archive={p:file_sha(p) for p in pin.parent.rglob('*') if p.is_file()}
    maintenance.approve(root,rid,review)
    guard=release.ReleaseGuard(checkpoint=pin);guard.verify()
    assert pin.read_bytes()==old
    assert all(file_sha(p)==sha for p,sha in archive.items())
    maintenance.approve(root,rid,review)
    current['unreviewed.py']='bad'
    with pytest.raises(ValueError):guard.verify()


@pytest.mark.parametrize('case',['unreviewed','bad_evidence','empty_evidence','rule','archive','wrong_request'])
def test_invalid_review_writes_no_approval(tmp_path,monkeypatch,case):
    root,rid,pin,review,current,manifest=reviewed(tmp_path,monkeypatch)
    if case=='unreviewed':manifest['changes']={}
    elif case=='bad_evidence':Path(manifest['validation_evidence'][0]['path']).write_text('changed')
    elif case=='empty_evidence':manifest['validation_evidence']=[]
    elif case=='wrong_request':manifest['request_id']='b'*64
    elif case=='rule':
        current['docs/campaign-rules.json']='bad'
        manifest['changes']=maintenance.differences(json.loads(pin.read_text())['source_hashes'],current)
    elif case=='archive':next((pin.parent/'execution-releases').glob('*/*')).write_text('corrupt')
    write(review,manifest)
    with pytest.raises(ValueError):maintenance.approve(root,rid,review)
    assert not (pin.parent/'execution-maintenance').exists()


@pytest.mark.parametrize('case',['review','checkpoint','archive'])
def test_approved_transition_rejects_tampering(tmp_path,monkeypatch,case):
    root,rid,pin,review,current,manifest=reviewed(tmp_path,monkeypatch)
    maintenance.approve(root,rid,review)
    if case=='review':review.write_text('{}')
    elif case=='checkpoint':(root/'process.log').write_text('changed')
    else:next((pin.parent/'execution-releases').glob('*/*')).write_text('corrupt')
    with pytest.raises(ValueError):release.ReleaseGuard(checkpoint=pin)
