import json
import sqlite3
from types import SimpleNamespace
import pytest
from campaign_discount_receipt import reconcile_discount
from campaign_discount_all_failed import failed_identity
from campaign_entry_authority import file_sha


def setup(tmp_path,monkeypatch):
    feedback=tmp_path/'failure.xlsx';feedback.write_bytes(b'unit fixture, parser mocked')
    rows=[dict(item='i',sku='s',message='already enrolled')]
    import campaign_discount_failure_report
    monkeypatch.setattr(campaign_discount_failure_report,'verify_failure_rows',lambda *a:rows)
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    db.executescript('CREATE TABLE claim_transports(claim_id,transport,state,job_id); CREATE TABLE attempts(id,phase,bundle_id,campaign,start,end);')
    db.execute('INSERT INTO claim_transports VALUES(?,?,?,?)',('c','dedicated_edge_v1','dispatched_unknown','j'))
    db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?)',('c:i','discount','b','campaign','start','end'))
    file=dict(path=str(tmp_path/'单品立减.xlsx'),sha256='original')
    body=dict(discount_rows=[dict(item='i',sku='s',deduct='1')],files=[file])
    accepted=[]
    authority=SimpleNamespace(db=db,get_bundle=lambda b:body,terminal=lambda c,d:accepted.append(d))
    result=dict(state='official_import_terminal',success=0,failed=1,start='start',end='end',
        text='活动创建成功，活动ID为：12345',offer_readback_gate='no_successful_offer_to_read',
        failure_rows=rows,feedback=dict(path=str(feedback),sha256=file_sha(feedback)),
        claim=dict(claim_id='c',file=file,items=['i'],campaign='campaign',phase='discount',start='start',end='end'))
    path=tmp_path/'discount-result.json';path.write_text(json.dumps(result));result['evidence_path']=str(path)
    job=dict(job_id='j',operation='discount',state='finished',result=result)
    return authority,job,accepted


def test_all_failed_terminal_not_success(tmp_path,monkeypatch):
    a,j,accepted=setup(tmp_path,monkeypatch)
    result=reconcile_discount(a,j,output_dir=tmp_path/'receipts')
    assert result['items']==[dict(item='i',outcome='failed')]
    assert result['batch']=='12345' and result['platform_write'] is False
    assert accepted[0]['successful_offer_verified'] is False
    assert accepted[0]['readbacks']==[]
    assert result['errors'][0]['message']=='already enrolled'


@pytest.mark.parametrize('change',[{'success':1},{'failed':2},{'text':'unknown'},
    {'text':'活动创建成功，活动ID为：12345 活动创建成功，活动ID为：67890'},
    {'offer_readback_gate':'pending'},{'readbacks':[{}]},{'success':False}])
def test_incomplete_or_ambiguous_is_not_failed_terminal(change):
    r=dict(state='official_import_terminal',success=0,failed=1,text='活动创建成功，活动ID为：12345',
           offer_readback_gate='no_successful_offer_to_read')
    r.update(change)
    with pytest.raises(ValueError):failed_identity(r,1)


def test_original_saved_observation_cannot_change(tmp_path,monkeypatch):
    a,j,accepted=setup(tmp_path,monkeypatch);j['result']['failed']=9
    with pytest.raises(ValueError,match='observation_changed'):reconcile_discount(a,j,output_dir=tmp_path/'receipts')
    assert accepted==[]


def test_failure_report_must_cover_every_submitted_sku(tmp_path,monkeypatch):
    a,j,accepted=setup(tmp_path,monkeypatch)
    import campaign_discount_failure_report
    monkeypatch.setattr(campaign_discount_failure_report,'verify_failure_rows',lambda *a:[])
    with pytest.raises(ValueError):reconcile_discount(a,j,output_dir=tmp_path/'receipts')
    assert accepted==[]
