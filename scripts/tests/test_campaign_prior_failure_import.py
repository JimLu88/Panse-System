import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import campaign_prior_failure_import as importer
from campaign_continuous_transport import persist
from campaign_entry_authority import file_sha


def test_import_only_latest_exact_window_failed_unprotected(tmp_path,monkeypatch):
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE attempts(id,item,campaign,phase,start,end,status,evidence,bundle_id)')
    for cid,item,start,status in [('old','1','start','failed'),('new','1','start','failed'),
                                  ('x','2','other','failed'),('p','3','start','failed'),('u','4','start','unknown')]:
        db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?)',(cid+':'+item,item,'1/2/3','signup',start,'end',status,'{}','b'))
    a=SimpleNamespace(db=db,blocked=lambda *x:{'3':'success'})
    t=SimpleNamespace(root=tmp_path,authority=a)
    adopt=Mock(return_value={'batch':'99','errors':[{'item':'1'}]})
    monkeypatch.setattr(importer,'adopt_report',adopt)
    result=importer.import_prior(t,{'identity':dict(campaign_id='1',phase_id='2',sign_record_id='3',start='start',end='end')},['1','2','3','4'])
    assert set(result)=={'1'} and adopt.call_args.args[2]=='new'
    assert adopt.call_count==1


@pytest.mark.parametrize('legacy',[False,True])
@pytest.mark.parametrize('restored',[False,True])
@pytest.mark.parametrize('bad',[None,'hash','window','claim','price','scope','outside','report_hash'])
def test_adoption_checks_claim_file_report_and_current_snapshot(tmp_path,monkeypatch,bad,legacy,restored):
    db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE attempts(id,item,status,bundle_id)')
    db.execute('INSERT INTO attempts VALUES(?,?,?,?)',('c:1','1','failed','b'))
    f=Path(persist(tmp_path/'submitted.xlsx',{}));report=Path(persist(tmp_path/'report.xlsx',{}))
    body={'campaign':'1/2/3','start':'start','end':'end','signup_rows':[{'item':'1','sku':'10','activity_price':'100'}],
          'files':[{'path':str(f),'sha256':file_sha(f)}],'target':'big'}
    doc={'schema':'campaign_entry_terminal_v1','claim_id':'wrong' if bad=='claim' else 'c','campaign':'1/2/3',
         'phase':'signup','start':'wrong' if bad=='window' else 'start','end':'end','terminal':True,'batch_id':'99',
         'items':[{'item':'1','status':'failed'}],'file_sha256':file_sha(f),
         'source_report':str(report),'source_report_sha256':file_sha(report)}
    if restored:doc['source_report']=str(tmp_path/'missing.xlsx')
    if bad=='report_hash':doc['source_report_sha256']='bad'
    if legacy:doc['official_report_path']=doc.pop('source_report');doc['official_report_sha256']=doc.pop('source_report_sha256')
    source=Path(persist(tmp_path/'terminal.json',doc))
    ref={'path':str(source),'sha256':'bad' if bad=='hash' else file_sha(source),'batch':'99'}
    rows=[{'id':'c:1','item':'1','status':'failed','bundle_id':'b','evidence':json.dumps(ref)}]
    snapshot={'all_erp_rows':['current']};persist(tmp_path/'resolved-snapshot.json',snapshot)
    a=SimpleNamespace(db=db,get_bundle=lambda _:body,bases=lambda s:{'current':True})
    t=SimpleNamespace(root=tmp_path,roots=[tmp_path/'other' if bad=='outside' else tmp_path],authority=a)
    from campaign_continuous_policy import fingerprint
    jid=fingerprint(['feedback',{'identity':{'same':True},'batch':'99'}])
    t.identity=lambda p:{'same':True}
    t.edge=SimpleNamespace(status=Mock(return_value={'job_id':jid,'operation':'feedback','state':'finished',
        'result':{'state':'downloaded','batch':'99','sha256':file_sha(report),'path':str(report)}}))
    parsed={'outcomes':[{'item':'1','outcome':'failed'}],'groups':[{'rows':[{'item':'1','sku':'11' if bad=='scope' else '10',
                                                                                 'submitted_price':'99' if bad=='price' else '100'}]}]}
    monkeypatch.setattr(importer,'parse_feedback',Mock(return_value=parsed))
    norm=Mock(return_value=[{'item':'1','kind':'custom_price'}]);monkeypatch.setattr(importer,'normalize_errors',norm)
    payload={'identity':dict(campaign_id='1',phase_id='2',sign_record_id='3',start='start',end='end',official_rate='.12')}
    if bad:
        with pytest.raises(ValueError):importer.adopt_report(t,payload,'c',rows)
        norm.assert_not_called()
    else:
        result=importer.adopt_report(t,payload,'c',rows)
        assert result['batch']=='99'
        assert norm.call_args.kwargs['erp_rows']==['current']
        assert norm.call_args.kwargs['actual_discounts']==[]
        assert file_sha(source)==ref['sha256']
        assert t.edge.status.call_count==int(restored)


@pytest.mark.parametrize('doc',[
    {},{'official_report_path':'x'},
    {'source_report':'x','source_report_sha256':'a','official_report_path':'y','official_report_sha256':'a'},
    {'source_report':'x','source_report_sha256':'a','official_report_path':'x','official_report_sha256':'b'}])
def test_conflicting_or_missing_report_reference_rejected(doc):
    with pytest.raises(ValueError):importer.report_reference(doc)


@pytest.mark.parametrize('change',[{'state':'running'},{'operation':'signup'},{'job_id':'wrong'},
    {'result':{'state':'downloaded','batch':'98','sha256':'sha','path':'x'}},
    {'result':{'state':'downloaded','batch':'99','sha256':'wrong','path':'x'}}])
def test_restored_report_requires_exact_finished_same_sha(change):
    from campaign_continuous_policy import fingerprint
    jid=fingerprint(['feedback',{'identity':{'same':True},'batch':'99'}])
    job=dict(job_id=jid,state='finished',operation='feedback',result={'state':'downloaded','batch':'99','sha256':'sha','path':'x'})
    job.update(change)
    t=SimpleNamespace(identity=lambda p:{'same':True},edge=SimpleNamespace(status=Mock(return_value=job)))
    with pytest.raises(ValueError):importer.restored_report(t,{},'99','sha')


@pytest.mark.parametrize('bad',[None,'duplicate','missing','mismatch'])
def test_gap_readback_only_missing_pairs_and_no_edits(tmp_path,monkeypatch,bad):
    errors=[{'item':'1','sku':'10','parse_issue':'exact_existing_discount_readback_missing'},
            {'item':'2','sku':'20','kind':'custom_price'}]
    offer={'offer_id':'123','start':'start','end':'end','items':[{'item':'1','status':'success'}],
           'rows':[{'item':'1','sku':'10','deduct':'10'},{'item':'1','sku':'11','deduct':'20'}]}
    offers=[] if bad=='missing' else [offer,offer] if bad=='duplicate' else [offer]
    a=SimpleNamespace(discount_offers=lambda:offers)
    job=Mock(return_value={'state':'finished'})
    t=SimpleNamespace(authority=a,job=job,root=tmp_path,roots=[tmp_path],identity=lambda p:{'official':True})
    verify=Mock(return_value={'all_correct':bad!='mismatch','evidence':{'sha256':'proof'}})
    import campaign_discount_readback
    monkeypatch.setattr(campaign_discount_readback,'verify',verify)
    body={'campaign':'1/2/3','start':'start','end':'end'}
    if bad:
        with pytest.raises(ValueError):importer.read_discount_gaps(t,{'identity':{'shop_id':'shop'}},errors,body,tmp_path/'saved.json')
    else:
        result=importer.read_discount_gaps(t,{'identity':{'shop_id':'shop'}},errors,body,tmp_path/'saved.json')
        assert result['rows']==[{'item':'1','sku':'10','deduct':'10','offer_id':'123'}]
        assert job.call_args.args[0]=='discount_readback'
        assert job.call_args.args[1]['offers']==[{'offer_id':'123','item':'1','sku_ids':['10']}]
