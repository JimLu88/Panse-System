import copy
import json
import sqlite3

import pytest
import campaign_scope_checkpoint as checkpoint
import campaign_scope_maintenance as maintenance
from campaign_continuous_policy import fingerprint
from campaign_entry_authority import file_sha
from test_campaign_scope_maintenance import fixture, write, reviewed


def discount_fixture(tmp_path,monkeypatch):
    root,rid,_,_=fixture(tmp_path,monkeypatch)
    action='a'*64;claim_id='b'*32;jid=fingerprint(['discount',claim_id]);read_id='c'*64
    payload={'claim_id':claim_id};folder=root/'segments/segment/actions'/action
    write(folder/'request.json',{'step':'discount','payload':payload})
    write(folder/'claim.json',{'claim_id':claim_id})
    write(folder/'discount-job.json',{'step':'discount','job_id':jid})
    with sqlite3.connect(root/'controller.sqlite3') as db:
        db.execute("UPDATE continuous_campaign_actions SET step='discount',status='unknown',payload_sha=?",(fingerprint(payload),))
    agent=tmp_path/'agent';out=agent/'data/output/campaign-transfers'/read_id
    feedback=write(out/'report.xlsx',{'fixture':'hash check'})
    window={'offer_id':'123','start':'2026-11-20 00:00:00','end':'2026-11-27 19:59:59'}
    claim={'claim_id':claim_id,'sku_rows':[{'item':'1','sku':'2','deduct':'12.34'},{'item':'1','sku':'3','deduct':'12.34'}],
           'start':window['start'],'end':window['end']}
    terminal={'state':'verified_partial_offer_terminal','claim':claim,'success':1,'failed':1,**window,
              'feedback':feedback,'failure_rows':[{'item':'1','sku':'3','message':'invalid sku'}],
              'readbacks':[{'item':'1','values':{'2':'12.34'},'window':window}]}
    terminal_file=out/'reconciled-discount-terminal.json';write(terminal_file,terminal)
    read={'source_job_id':jid,'terminal':dict(terminal,evidence_path=str(terminal_file)),'recording':{'fixture':True}}
    current=dict(read['terminal'],recording=read['recording']);prior=dict(terminal,state='official_import_terminal')
    database=tmp_path/'transfer.sqlite'
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE campaign_transfer_jobs(id TEXT,state TEXT,operation TEXT,result TEXT)')
        db.execute('CREATE TABLE campaign_discount_terminal_recovery(id TEXT,read_job_id TEXT,previous_result TEXT,new_result TEXT)')
        db.execute('INSERT INTO campaign_transfer_jobs VALUES(?,?,?,?)',(jid,'finished','discount',json.dumps(current)))
        db.execute('INSERT INTO campaign_transfer_jobs VALUES(?,?,?,?)',(read_id,'finished','discount_terminal_recover',json.dumps(read)))
        db.execute('INSERT INTO campaign_discount_terminal_recovery VALUES(?,?,?,?)',(jid,read_id,json.dumps(prior),json.dumps(current)))
    return root,rid,database,agent,jid,current,read_id


@pytest.mark.parametrize('tamper',[None,'audit','count','read_state','amount','report','owner','request'])
def test_discount_checkpoint_requires_audited_original_success_and_preserves_every_file(tmp_path,monkeypatch,tamper):
    root,rid,dbpath,agent,jid,current,read_id=discount_fixture(tmp_path,monkeypatch)
    if tamper=='owner':
        with sqlite3.connect(root/'controller.sqlite3') as db:db.execute("UPDATE continuous_campaign_runs SET owner='other'")
    elif tamper=='request':write(root/'request.json',{})
    elif tamper=='report':write(agent/'data/output/campaign-transfers'/read_id/'report.xlsx',{'changed':True})
    elif tamper:
        with sqlite3.connect(dbpath) as db:
            if tamper=='audit':db.execute("UPDATE campaign_discount_terminal_recovery SET new_result='{}'")
            elif tamper=='read_state':db.execute("UPDATE campaign_transfer_jobs SET state='running' WHERE id=?",(read_id,))
            else:
                changed=copy.deepcopy(current)
                if tamper=='count':changed['success']=2
                if tamper=='amount':changed['readbacks'][0]['values']['2']='13.34'
                db.execute('UPDATE campaign_transfer_jobs SET result=? WHERE id=?',(json.dumps(changed),jid))
                db.execute('UPDATE campaign_discount_terminal_recovery SET new_result=?',(json.dumps(changed),))
    before={p:file_sha(p) for p in tmp_path.rglob('*') if p.is_file()}
    if tamper:
        with pytest.raises(ValueError):checkpoint.verify_discount(root,rid,transfer_db=dbpath,agent_root=agent)
    else:
        proof=checkpoint.verify_discount(root,rid,transfer_db=dbpath,agent_root=agent)
        assert proof['success']==proof['failed']==1 and proof['discount_job_id']==jid
        checkpoint.check_references(proof)
    assert before=={p:file_sha(p) for p in tmp_path.rglob('*') if p.is_file()}


def test_discount_maintenance_requires_verified_discount_and_preserves_pin(tmp_path,monkeypatch):
    root,rid,pin,review,_,_=reviewed(tmp_path,monkeypatch)
    original=pin.read_bytes();calls=[]
    def proof(r,i):
        calls.append((r,i));return {'schema':'campaign_discount_checkpoint_v1','request_id':rid,'references':[]}
    monkeypatch.setattr(checkpoint,'verify_discount',proof)
    result=maintenance.approve(root,rid,review,phase='discount')
    assert calls==[(root,rid)] and result['platform_write'] is False and pin.read_bytes()==original
    with pytest.raises(ValueError):maintenance.approve(root,rid,review,phase='other')
